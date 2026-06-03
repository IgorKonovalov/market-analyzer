/**
 * Plan 0030 phase 2 — deterministic seeded-cache lazy-history e2e (the
 * close-blocking gate).
 *
 * This proves the renderer↔sidecar wiring for scroll-left lazy loading
 * WITHOUT depending on Plan 0031 (the Yahoo absolute-range data-layer fix).
 * The trick: we seed the sidecar's SQLite cache with a contiguous range of
 * daily bars for a SYNTHETIC symbol (`SEEDCO`) that real Yahoo does not list.
 * Because the older windows the renderer requests are fully cache-covered,
 * `GET /ohlcv` serves them straight from SQLite — no gap, so the provider
 * never reaches out to Yahoo. And because `SEEDCO` is unlisted, any accidental
 * escape to a live fetch fails LOUDLY (502 → `olderError` chip) rather than
 * silently masking a broken trigger. So this spec isolates exactly the path
 * Plan 0030 added: scroll → visible-range trigger → `loadOlder` → `/ohlcv` →
 * prepend → anchored viewport.
 *
 * The seed helper mirrors `ohlcv-view.spec.ts`'s `insertAnnotation`: a Python
 * subprocess that writes to the SAME DB the launched sidecar reads. Under
 * `_electron.launch` (unpackaged), Electron passes
 * `MARKET_ANALYSER_DATA_DIR=<userData>` to the spawned sidecar, and that
 * userData path (`<Roaming>/Electron/`) diverges from Python's bare
 * `default_app_data_dir()` (`<Roaming>/market-analyser/`). So we resolve the
 * DB via `load_config(None).db_path` under that same env var — exactly as
 * `insertAnnotation` does — rather than the plan's literal
 * `default_app_data_dir()`, which would seed the wrong file.
 *
 * Why EVERY calendar day (not just weekdays): the provider's `_coverage_gaps`
 * always treats an uncovered head/tail RIM of the requested `[start, end]` as a
 * fetch gap (only INTERNAL gaps below the 10-bar threshold are skipped as
 * weekend/holiday closures). And an unlisted symbol's fetch does NOT return a
 * clean empty — `YahooAdapter` raises `UnknownSymbolError`, which the loud
 * `GET /ohlcv` path surfaces as an error, not a silent no-op. So to keep every
 * renderer-issued window (the initial load AND each `loadOlder` chunk) fully
 * cache-covered with ZERO rim gaps, a seeded bar must sit at exactly each
 * window edge. Seeding every calendar day at midnight UTC guarantees that as
 * long as the window edges are day-aligned midnights inside the seeded range —
 * which they are: `show_chart` edges are `isoMidnightUtc(...)`, and `loadOlder`
 * fetches `[earliest - chunkSpanMs, earliest]` where `earliest` is a seeded
 * midnight and `chunkSpanMs` is the (whole-day) initial-window span, so its
 * `start` is also a seeded midnight.
 *
 * Distinct from the demoted best-effort live scroll case in
 * `live-chart.spec.ts` (which hits REAL Yahoo and is gated on the affordance
 * appearing): this one is deterministic and unconditionally asserts growth.
 */
import { _electron as electron, test, expect, type ElectronApplication } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(__dirname, '..', '..')

const SEED_SYMBOL = 'SEEDCO'
const SEED_TIMEFRAME = '1d'

interface ChartRenderSnapshot {
  seriesCount: number
  seriesKinds: Array<{ kind: string; period?: number | null }>
  barCount: number
}

/**
 * Seed a contiguous run of daily `Bar`s for `SEEDCO` into the SAME SQLite DB
 * the launched sidecar uses, via a Python subprocess using
 * `BarRepository.upsert_bars`. `dataDir` MUST be the live Electron's
 * `app.getPath('userData')` (see file header for why) — we forward it as
 * `MARKET_ANALYSER_DATA_DIR` so `load_config(None).db_path` resolves to the
 * file the spawned sidecar reads.
 *
 * Seeds EVERY calendar day (see file header for why) from `startIso` to
 * `endIso` inclusive at midnight UTC. The synthetic OHLC is a slow
 * deterministic walk so the chart draws something sane and the `Bar`
 * validators (low ≤ open/close ≤ high) pass. Returns the bar count written.
 */
function seedBars(startIso: string, endIso: string, dataDir: string): number {
  const script = [
    'import json, sys',
    'from datetime import datetime, timedelta',
    'from market_analyser.config import load_config',
    'from market_analyser.data.types import Bar',
    'from market_analyser.persistence.engine import apply_migrations, make_engine, make_session_factory',
    'from market_analyser.persistence.repository import BarRepository',
    'req = json.loads(sys.stdin.read())',
    'symbol, timeframe = req["symbol"], req["timeframe"]',
    'start = datetime.fromisoformat(req["start"])',
    'end = datetime.fromisoformat(req["end"])',
    'cfg = load_config(None)',
    'engine = make_engine(cfg.db_path)',
    'apply_migrations(engine)',
    'repo = BarRepository(make_session_factory(engine))',
    'bars = []',
    'day = start',
    'i = 0',
    'while day <= end:',
    '    base = 100.0 + (i % 50) * 0.5',
    '    o = base',
    '    c = base + 0.75',
    '    hi = c + 0.5',
    '    lo = o - 0.5',
    '    bars.append(Bar(symbol=symbol, timeframe=timeframe, event_ts=day, open=o, high=hi, low=lo, close=c, volume=1000.0 + i, source="e2e-seed"))',
    '    i += 1',
    '    day = day + timedelta(days=1)',
    'written = repo.upsert_bars(bars)',
    'print(written)',
  ].join('\n')

  const result = spawnSync('uv', ['run', '--no-sync', 'python', '-c', script], {
    cwd: REPO_ROOT,
    input: JSON.stringify({
      symbol: SEED_SYMBOL,
      timeframe: SEED_TIMEFRAME,
      start: startIso,
      end: endIso,
    }),
    encoding: 'utf-8',
    shell: false,
    env: { ...process.env, MARKET_ANALYSER_DATA_DIR: dataDir },
  })
  if (result.status !== 0) {
    throw new Error(`seedBars failed (exit ${result.status}): ${result.stderr}`)
  }
  return Number(result.stdout.trim())
}

interface ToolResult {
  isError: boolean
  content: string[]
  structured: Record<string, unknown> | null
}

/**
 * Drive the running sidecar's MCP `show_chart` tool from a Python subprocess.
 * Mirrors `live-chart.spec.ts`'s `callMcpTool` — reads the lockfile + secret
 * from `default_app_data_dir()`. Under `_electron.launch` the sidecar's
 * lockfile lives in Electron's userData dir, so we forward the same
 * `MARKET_ANALYSER_DATA_DIR` we seeded with.
 */
function callMcpTool(tool: string, args: Record<string, unknown>, dataDir: string): ToolResult {
  const script = [
    'import asyncio, json, sys',
    'from market_analyser.api.lockfile import read_lockfile',
    'from market_analyser.api.mcp_secret import read_secret_record',
    'from market_analyser.config import default_app_data_dir',
    'import httpx',
    'from mcp import ClientSession',
    'from mcp.client.streamable_http import streamable_http_client',
    'app_data = default_app_data_dir()',
    'lock = read_lockfile(app_data / "sidecar.lock")',
    'assert lock is not None, f"no sidecar.lock at {app_data}"',
    'secret = read_secret_record(app_data / "mcp-secret.json").secret',
    'req = json.loads(sys.stdin.read())',
    'tool, args = req["tool"], req["args"]',
    'async def run():',
    '    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {secret}"}, timeout=httpx.Timeout(30.0)) as http_client:',
    '        async with streamable_http_client(f"http://127.0.0.1:{lock.port}/mcp", http_client=http_client) as (read_stream, write_stream, _):',
    '            async with ClientSession(read_stream, write_stream) as session:',
    '                await session.initialize()',
    '                r = await session.call_tool(tool, args)',
    '                return {"isError": r.isError, "content": [str(c) for c in r.content], "structured": r.structuredContent}',
    'print(json.dumps(asyncio.run(run())))',
  ].join('\n')

  const result = spawnSync('uv', ['run', '--no-sync', 'python', '-c', script], {
    cwd: REPO_ROOT,
    input: JSON.stringify({ tool, args }),
    encoding: 'utf-8',
    shell: false,
    env: { ...process.env, MARKET_ANALYSER_DATA_DIR: dataDir },
  })
  if (result.status !== 0) {
    throw new Error(`callMcpTool(${tool}) failed (exit ${result.status}): ${result.stderr}`)
  }
  return JSON.parse(result.stdout) as ToolResult
}

async function readChartRender(
  window: import('@playwright/test').Page,
): Promise<ChartRenderSnapshot> {
  const snapshot = await window.evaluate(
    () => (globalThis as { __test_chart_render__?: unknown }).__test_chart_render__,
  )
  if (!snapshot) throw new Error('__test_chart_render__ not exposed on window')
  return snapshot as ChartRenderSnapshot
}

async function getDataDir(app: ElectronApplication): Promise<string> {
  return app.evaluate(({ app: electronApp }) => electronApp.getPath('userData'))
}

async function launchApp(): Promise<ElectronApplication> {
  return electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
  })
}

/** YYYY-MM-DDT00:00:00+00:00 for a Date, as `show_chart`/seed both expect. */
function isoMidnightUtc(d: Date): string {
  const day = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()))
  return day.toISOString().replace('.000Z', '+00:00')
}

test('scrolling to the left edge prepends seeded older bars (deterministic, no Yahoo)', async () => {
  const app = await launchApp()
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')
  const dataDir = await getDataDir(app)

  // Capture every /ohlcv request so we can prove a SECOND (older-window) fetch
  // was issued by `loadOlder` — the network-level evidence the scroll trigger
  // fired, independent of the (fast, local) loading affordance's visibility.
  const ohlcvUrls: string[] = []
  window.on('request', (req) => {
    const url = req.url()
    if (/^http:\/\/127\.0\.0\.1:\d+\/ohlcv\?/.test(url)) ohlcvUrls.push(url)
  })

  // Seed ~3 years of contiguous daily bars ending ~now, so the initial narrow
  // window and many older chunks are all fully cache-covered.
  const now = new Date()
  const seedEnd = now
  const seedStart = new Date(now)
  seedStart.setUTCFullYear(seedStart.getUTCFullYear() - 3)
  const written = seedBars(isoMidnightUtc(seedStart), isoMidnightUtc(seedEnd), dataDir)
  // 3y of calendar days ≈ 1096 bars; assert a healthy floor so a silently-empty
  // seed (wrong DB path) fails here rather than later as a confusing no-growth.
  expect(written).toBeGreaterThan(1000)

  // Wait for the renderer to mount (the reducer's chart-state snapshot appears).
  await expect
    .poll(
      () =>
        window.evaluate(
          () =>
            (globalThis as { __test_chart_state__?: { symbol?: string } }).__test_chart_state__
              ?.symbol,
        ),
      { timeout: 15_000, intervals: [200] },
    )
    .toBeDefined()

  // Show SEEDCO on the most-recent ~1 month of the seeded range. The mount-time
  // fit sits at the left edge of this narrow window, so the lazy trigger fires
  // on show; the leftward drags below are belt-and-suspenders.
  const showStart = new Date(now)
  showStart.setUTCMonth(showStart.getUTCMonth() - 1)
  callMcpTool(
    'show_chart',
    {
      symbol: SEED_SYMBOL,
      timeframe: SEED_TIMEFRAME,
      range_start: isoMidnightUtc(showStart),
      range_end: isoMidnightUtc(now),
    },
    dataDir,
  )

  // The candlestick series renders from the seeded SQLite bars.
  await expect
    .poll(async () => (await readChartRender(window)).seriesCount, {
      timeout: 15_000,
      intervals: [200],
    })
    .toBeGreaterThanOrEqual(1)
  const initialBarCount = (await readChartRender(window)).barCount
  expect(initialBarCount).toBeGreaterThan(0)

  // Latch whether the left-edge loading affordance EVER appears. The older
  // `/ohlcv` resolves from local SQLite in well under a poll interval, so a
  // `locator.count()` poll routinely misses the brief `isLoadingOlder` flip
  // (the wiring is correct — the DOM element just mounts and unmounts between
  // samples). A `MutationObserver` installed BEFORE the trigger catches the
  // node addition synchronously, regardless of fetch speed. (The deterministic
  // "renders iff isLoadingOlder" claim is owned by the Jest OhlcvView spec;
  // here we prove the same affordance lights up against the real wiring.)
  await window.evaluate(() => {
    const w = globalThis as { __sawHistoryLoading__?: boolean }
    w.__sawHistoryLoading__ = false
    const sel = '[data-testid="ohlcv-history-loading"]'
    if (document.querySelector(sel)) w.__sawHistoryLoading__ = true
    const obs = new MutationObserver(() => {
      if (document.querySelector(sel)) w.__sawHistoryLoading__ = true
    })
    obs.observe(document.body, { childList: true, subtree: true })
  })

  // Nudge the viewport hard toward the left edge (drag content rightward to
  // reveal earlier time), in case the initial fit didn't already trip the
  // trigger.
  const chart = window.locator('[data-testid="candlestick-chart"]')
  const box = await chart.boundingBox()
  if (box) {
    const y = box.y + box.height / 2
    for (let i = 0; i < 2; i++) {
      await window.mouse.move(box.x + box.width * 0.3, y)
      await window.mouse.down()
      await window.mouse.move(box.x + box.width * 0.95, y, { steps: 10 })
      await window.mouse.up()
    }
  }

  // (b) The candlestick series grew — older seeded bars were prepended. This is
  // the substantive end-to-end claim: scroll → trigger → loadOlder → /ohlcv →
  // prepend. Deterministic because the older window is fully seeded.
  await expect
    .poll(async () => (await readChartRender(window)).barCount, {
      timeout: 10_000,
      intervals: [100],
    })
    .toBeGreaterThan(initialBarCount)

  // (a) The loading affordance appeared (then cleared) during that fetch.
  const sawLoading = await window.evaluate(
    () => (globalThis as { __sawHistoryLoading__?: boolean }).__sawHistoryLoading__ === true,
  )
  expect(sawLoading).toBe(true)
  await expect(window.locator('[data-testid="ohlcv-history-loading"]')).toHaveCount(0)

  // The error chip must NOT show — a fully-cached older window never touches
  // Yahoo, so an unlisted-symbol error here would mean the fetch escaped the
  // cache (the bug this seeded setup is designed to catch).
  await expect(window.locator('[data-testid="ohlcv-history-error"]')).toHaveCount(0)

  // Network-level proof the trigger fired: a SECOND /ohlcv (the older chunk)
  // beyond the initial-window load.
  expect(ohlcvUrls.length).toBeGreaterThanOrEqual(2)

  await app.close()
})
