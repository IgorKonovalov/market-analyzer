/**
 * Plan 0001 phase 5 done-when: candlestick chart for one symbol.
 *
 * Two distinct claims, kept as separate tests (Plan 0072 phase 7):
 *  - Happy path (deterministic): with a seeded, network-free fixture, the chart
 *    renders — `chartVisible` with a candlestick series and bars.
 *  - Resilience (no-hang): with the default symbol and whatever the network
 *    gives, OhlcvView always resolves to a DEFINITE state — chart, error, or
 *    empty — never an infinite spinner. This one intentionally accepts any of
 *    the three; it is a liveness claim, not a happy-path claim.
 *
 * Requires a built desktop bundle. Run with `pnpm --filter desktop test:e2e`.
 */
import { _electron as electron, test, expect, type ElectronApplication } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(__dirname, '..', '..')

interface InsertAnnotationArgs {
  symbol: string
  timeframe: string
  event_ts: string
  kind: 'bullish_marker' | 'bearish_marker'
  label: string
}

/**
 * Insert an annotation into the sidecar's SQLite DB via a Python subprocess
 * that targets the SAME data dir the running sidecar is using. Plan 0006
 * phase 6 uses this to seed annotations without going through the MCP
 * transport.
 *
 * `dataDir` MUST be the live Electron's `app.getPath('userData')`, not
 * Python's `default_app_data_dir()` — Plan 0007 phase 1 made Electron pass
 * `MARKET_ANALYSER_DATA_DIR=<userData>` to the spawned sidecar, and under
 * `_electron.launch` (unpackaged) those two paths diverge
 * (`<Roaming>/Electron/` vs `<Roaming>/market-analyser/`).
 *
 * Returns the inserted annotation's id.
 */
function insertAnnotation(args: InsertAnnotationArgs, dataDir: string): string {
  const script = [
    'import json, sys',
    'from datetime import datetime',
    'from market_analyser.annotations.types import Annotation, AnnotationKind',
    'from market_analyser.config import load_config',
    'from market_analyser.persistence.annotations_repository import AnnotationsRepository',
    'from market_analyser.persistence.engine import apply_migrations, make_engine, make_session_factory',
    'cfg = load_config(None)',
    'engine = make_engine(cfg.db_path)',
    'apply_migrations(engine)',
    'repo = AnnotationsRepository(make_session_factory(engine))',
    'raw = json.loads(sys.stdin.read())',
    'a = Annotation(symbol=raw["symbol"], timeframe=raw["timeframe"], event_ts=datetime.fromisoformat(raw["event_ts"]), kind=AnnotationKind(raw["kind"]), label=raw.get("label"), agent_id="e2e")',
    'repo.insert(a)',
    'print(a.id)',
  ].join('; ')

  const result = spawnSync('uv', ['run', '--no-sync', 'python', '-c', script], {
    cwd: REPO_ROOT,
    input: JSON.stringify(args),
    encoding: 'utf-8',
    shell: false,
    env: { ...process.env, MARKET_ANALYSER_DATA_DIR: dataDir },
  })
  if (result.status !== 0) {
    throw new Error(`insertAnnotation failed (exit ${result.status}): ${result.stderr}`)
  }
  return result.stdout.trim()
}

async function getDataDir(app: ElectronApplication): Promise<string> {
  return app.evaluate(({ app: electronApp }) => electronApp.getPath('userData'))
}

/**
 * Latest `end` (ms) among the captured /ohlcv request URLs at or after
 * `fromIndex`, or -Infinity if none carry a parseable `end`.
 *
 * The chart's lazy-history loader (Plan 0030) fires its own /ohlcv requests on
 * mount, reaching BACKWARD to older windows (an EARLIER `end`). So the Refresh
 * fetch can't be identified by request index — it's identified by its window end
 * advancing to ~now. Taking the max end over post-click requests ignores any
 * interleaved lazy-history reads (whose ends stay below the initial load's).
 */
function latestEndMs(urls: string[], fromIndex: number): number {
  const ends = urls
    .slice(fromIndex)
    .map((u) => new URL(u).searchParams.get('end'))
    .filter((e): e is string => e !== null)
    .map((e) => new Date(e).getTime())
    .filter((n) => !Number.isNaN(n))
  return ends.length > 0 ? Math.max(...ends) : Number.NEGATIVE_INFINITY
}

// --- deterministic seeded happy-path helpers (mirror lazy-history.spec.ts) --- //
// A synthetic symbol real Yahoo does not list: its cache is seeded here, and an
// unlisted symbol's upstream fetch returns nothing, so the chart renders purely
// from the seeded SQLite bars — no network, no flake.
const SEED_SYMBOL = 'SEEDCO'
const SEED_TIMEFRAME = '1d'

interface ChartRenderSnapshot {
  seriesCount: number
  barCount: number
}

/**
 * Seed contiguous daily `Bar`s for `SEEDCO` into the SAME SQLite DB the launched
 * sidecar uses, via a Python subprocess (`BarRepository.upsert_bars`). `dataDir`
 * MUST be the live Electron's `userData` (forwarded as `MARKET_ANALYSER_DATA_DIR`
 * so `load_config(None).db_path` resolves to the sidecar's file). Every calendar
 * day is seeded so the requested window is cache-covered with no rim gaps.
 * Returns the bar count written.
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
 * Drive the running sidecar's MCP `show_chart` tool from a Python subprocess so
 * the renderer displays the seeded symbol. Reads the lockfile + secret from the
 * same `MARKET_ANALYSER_DATA_DIR` we seeded with.
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

/** YYYY-MM-DDT00:00:00+00:00 for a Date, as `show_chart`/seed both expect. */
function isoMidnightUtc(d: Date): string {
  const day = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()))
  return day.toISOString().replace('.000Z', '+00:00')
}

test('happy path: a seeded fixture renders the candlestick chart (deterministic, no Yahoo)', async () => {
  const app = await electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
  })
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')
  const dataDir = await getDataDir(app)

  await expect(window.getByRole('region', { name: /OHLCV view/ })).toBeVisible({
    timeout: 15_000,
  })

  // Seed ~4 months of contiguous daily bars for SEEDCO ending ~now, then show
  // a window fully inside the seeded range so the chart is cache-covered with
  // no rim gaps — SEEDCO is unlisted upstream, so nothing is fetched.
  const now = new Date()
  const seedStart = new Date(now)
  seedStart.setUTCMonth(seedStart.getUTCMonth() - 4)
  const written = seedBars(isoMidnightUtc(seedStart), isoMidnightUtc(now), dataDir)
  expect(written).toBeGreaterThan(100)

  // Wait for the renderer to mount before driving show_chart.
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

  // The happy path, asserted specifically: the candlestick chart is visible
  // with a real canvas AND the series drew bars from the seeded cache.
  const chart = window.locator('[data-testid="candlestick-chart"]')
  await expect(chart).toBeVisible({ timeout: 15_000 })
  expect(await chart.locator('canvas').count()).toBeGreaterThan(0)
  await expect
    .poll(async () => (await readChartRender(window)).seriesCount, {
      timeout: 15_000,
      intervals: [200],
    })
    .toBeGreaterThanOrEqual(1)
  expect((await readChartRender(window)).barCount).toBeGreaterThan(0)

  await app.close()
})

test('resilience: cold launch resolves OhlcvView to a definite state (never an infinite spinner)', async () => {
  const app = await electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
  })
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')

  // First prove OhlcvView mounted — independent of whether the sidecar fetch
  // succeeds. The section has a stable aria-label.
  await expect(window.getByRole('region', { name: /OHLCV view/ })).toBeVisible({
    timeout: 15_000,
  })

  // Then wait for the chart canvas OR a visible error state OR an empty
  // state. This is deliberately a THREE-way accept: it is the liveness claim
  // that the useOhlcv hook always resolves to a definite state (success,
  // surfaced failure, or no-bars) and never hangs on an infinite spinner. The
  // deterministic happy path is the seeded test above; the empty branch is a
  // real user state (offline + uncached symbol + bad range), which Plan 0004
  // phase 7 gave a role+testid so this predicate matches it directly.
  const chart = window.locator('[data-testid="candlestick-chart"]')
  const errorState = window.getByRole('alert')
  const emptyState = window.locator('[data-testid="ohlcv-empty"]')
  await expect(async () => {
    const chartVisible = await chart.isVisible().catch(() => false)
    const errorVisible = await errorState.isVisible().catch(() => false)
    const emptyVisible = await emptyState.isVisible().catch(() => false)
    expect(chartVisible || errorVisible || emptyVisible).toBe(true)
  }).toPass({ timeout: 30_000 })

  await app.close()
})

test('Refresh advances the OHLCV window end timestamp', async () => {
  const app = await electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
  })
  const window = await app.firstWindow()

  // Attach the request listener BEFORE awaiting load — the initial /ohlcv
  // fetch fires as soon as OhlcvView mounts, and we need to capture it.
  const ohlcvUrls: string[] = []
  window.on('request', (req) => {
    const url = req.url()
    if (/^http:\/\/127\.0\.0\.1:\d+\/ohlcv\?/.test(url)) {
      ohlcvUrls.push(url)
    }
  })

  await window.waitForLoadState('domcontentloaded')
  await expect(window.getByRole('region', { name: /OHLCV view/ })).toBeVisible({
    timeout: 15_000,
  })

  // Wait for the initial fetch to land before clicking Refresh.
  await expect.poll(() => ohlcvUrls.length, { timeout: 30_000 }).toBeGreaterThanOrEqual(1)
  const firstEnd = new URL(ohlcvUrls[0]).searchParams.get('end')
  expect(firstEnd).not.toBeNull()
  const firstEndMs = new Date(firstEnd!).getTime()

  // Snapshot the request count before the click. The next request by index is
  // NOT guaranteed to be the Refresh fetch — the lazy-history loader (Plan 0030)
  // interleaves its own /ohlcv reads with an earlier `end` (see latestEndMs).
  const beforeRefresh = ohlcvUrls.length

  // Tiny pause so Date.now() definitely differs between the two memo computes.
  await window.waitForTimeout(50)

  const refreshButton = window.getByRole('button', { name: 'Refresh' })
  await expect(refreshButton).toBeEnabled({ timeout: 15_000 })
  await refreshButton.click()

  // Refresh advances range_end to ~now, so its /ohlcv request carries an `end`
  // strictly later than the initial load's. Wait for a post-click request whose
  // end advanced past firstEnd, ignoring any interleaved lazy-history reads.
  await expect
    .poll(() => latestEndMs(ohlcvUrls, beforeRefresh), { timeout: 15_000 })
    .toBeGreaterThan(firstEndMs)

  await app.close()
})

test('annotation written to the DB surfaces on the renderer within a poll window', async () => {
  const app = await electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
  })
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')
  const dataDir = await getDataDir(app)

  // Wait for the chart view to mount so the poll loop is alive.
  await expect(window.getByRole('region', { name: /OHLCV view/ })).toBeVisible({
    timeout: 15_000,
  })

  // Capture annotation responses so we can assert the renderer received our row.
  const annotationsResponses: Array<{ url: string; body: string }> = []
  window.on('response', async (response) => {
    const url = response.url()
    if (!/127\.0\.0\.1:\d+\/annotations\?/.test(url)) return
    try {
      const body = await response.text()
      annotationsResponses.push({ url, body })
    } catch {
      // ignore — response may already be discarded
    }
  })

  // Insert a bullish annotation at a recent date inside the default 365-day
  // window. Mid-yesterday-UTC keeps it inside the OhlcvView's lookback.
  const eventDate = new Date(Date.now() - 24 * 60 * 60 * 1000)
  eventDate.setUTCHours(12, 0, 0, 0)
  const expectedLabel = `e2e-marker-${Date.now()}`
  const annotationId = insertAnnotation(
    {
      symbol: 'AAPL',
      timeframe: '1d',
      event_ts: eventDate.toISOString(),
      kind: 'bullish_marker',
      label: expectedLabel,
    },
    dataDir,
  )
  expect(annotationId).toMatch(/^[0-9a-f]{32}$/)

  // Within ~2 poll cycles, the renderer's GET /annotations should include
  // the inserted row.
  await expect
    .poll(() => annotationsResponses.some((r) => r.body.includes(annotationId)), {
      timeout: 5_000,
      intervals: [200],
    })
    .toBe(true)

  // Cross-check: the matching response also carries the label verbatim, so
  // the marker rendered on the chart would have the right tooltip text.
  const matched = annotationsResponses.find((r) => r.body.includes(annotationId))
  expect(matched).toBeDefined()
  expect(matched!.body).toContain(expectedLabel)

  // And the chart canvas is still present (marker rendering layers on, doesn't
  // recreate the chart).
  await expect(window.locator('[data-testid="candlestick-chart"]')).toBeVisible()

  await app.close()
})
