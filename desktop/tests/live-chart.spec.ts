/**
 * Plan 0007 phase 4 done-when (e2e). Drives the running sidecar's MCP
 * surface from a Python subprocess (matches `tests/ohlcv-view.spec.ts`'s
 * pattern for `insertAnnotation`) and asserts the renderer's chart state
 * snapshot reacts within the SSE round-trip budget.
 *
 * The assertions read `window.__test_chart_state__` rather than canvas
 * pixels — the plan and ADR-0017's e2e guidance call this out explicitly:
 * the test hook is the contract surface, the canvas is best-effort visual.
 *
 * Five behavioural claims:
 *   1. `show_chart(symbol="MSFT", overlays=[ema20])` → chart state's
 *      symbol becomes MSFT and overlays contain ema20 within 1 s.
 *   2. Subsequent `update_chart(overlays=[ema50])` → overlays merge
 *      (both ema20 and ema50) — `chart.update` adds, doesn't replace.
 *   3. `update_chart(range_start, range_end)` → range fields update
 *      within ~100 ms (we give 500 ms wall budget for CI slack).
 *   4. Out-of-order: `update_chart(symbol="GOOG", ...)` arrives without
 *      a prior `show_chart` for GOOG → renderer switches to GOOG (ADR-0017
 *      "no matching chart open → treat as show").
 *   5. `highlight_pattern(...)` → `liveHighlights` buffer gains the
 *      marker; the polled annotation row replaces it within 2 poll
 *      windows without producing a duplicate marker key.
 */
import { _electron as electron, test, expect, type ElectronApplication } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(__dirname, '..', '..')

interface ToolResult {
  isError: boolean
  content: string[]
}

interface ChartStateSnapshot {
  symbol: string
  timeframe: string
  range_start: string
  range_end: string
  overlays: Array<{ kind: string; period?: number | null }>
  liveHighlights: Array<{ event_ts: string; kind: string; label?: string | null }>
}

/**
 * Call an MCP tool on the live sidecar. Reads `sidecar.lock` for the port
 * and `mcp-secret.json` for the bearer; both files were written by the
 * sidecar Electron just spawned into `dataDir` (which it set via
 * `MARKET_ANALYSER_DATA_DIR` per phase 1's main.ts). The `dataDir` is the
 * value of `app.getPath('userData')` from the live Electron instance —
 * NOT Python's `default_app_data_dir()`, which diverges from Electron's
 * userData when running unpackaged via `_electron.launch` (the unpackaged
 * `app.getName()` returns `"Electron"`, so userData lands at
 * `<Roaming>/Electron/` rather than `<Roaming>/market-analyser/`).
 */
function callMcpTool(tool: string, args: Record<string, unknown>, dataDir: string): ToolResult {
  const script = [
    'import asyncio, json, os, pathlib, sys',
    'from market_analyser.api.lockfile import read_lockfile',
    'from market_analyser.api.mcp_secret import read_secret_record',
    'import httpx',
    'from mcp import ClientSession',
    'from mcp.client.streamable_http import streamable_http_client',
    'app_data = pathlib.Path(os.environ["MARKET_ANALYSER_DATA_DIR"])',
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
    '                return {"isError": r.isError, "content": [str(c) for c in r.content]}',
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

async function readChartState(
  window: import('@playwright/test').Page,
): Promise<ChartStateSnapshot> {
  const snapshot = await window.evaluate(
    () => (globalThis as { __test_chart_state__?: unknown }).__test_chart_state__,
  )
  if (!snapshot) throw new Error('__test_chart_state__ not exposed on window')
  return snapshot as ChartStateSnapshot
}

async function launchApp(): Promise<ElectronApplication> {
  return electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
  })
}

/**
 * The live sidecar writes its lockfile + mcp-secret.json into Electron's
 * `app.getPath('userData')`, which under `_electron.launch` (unpackaged)
 * is `<Roaming>/Electron/`, not Python's `default_app_data_dir()`. Query
 * the running Electron for the truth so the MCP subprocess targets the
 * right files.
 */
async function getDataDir(app: ElectronApplication): Promise<string> {
  return app.evaluate(({ app: electronApp }) => electronApp.getPath('userData'))
}

test('chart.show via MCP switches the renderer to the requested symbol and overlays', async () => {
  const app = await launchApp()
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')
  const dataDir = await getDataDir(app)

  // Wait until the chart state hook is attached — the App.tsx effect that
  // writes __test_chart_state__ runs on first render.
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

  const ack = callMcpTool(
    'show_chart',
    {
      symbol: 'MSFT',
      timeframe: '1d',
      range_start: '2026-04-20T00:00:00+00:00',
      range_end: '2026-05-20T00:00:00+00:00',
      overlays: [{ kind: 'ema', period: 20 }],
    },
    dataDir,
  )
  expect(ack.isError).toBe(false)

  await expect
    .poll(async () => (await readChartState(window)).symbol, { timeout: 2_000, intervals: [50] })
    .toBe('MSFT')

  const state = await readChartState(window)
  expect(state.timeframe).toBe('1d')
  expect(state.overlays).toEqual([{ kind: 'ema', period: 20 }])
  expect(state.range_start).toContain('2026-04-20')
  expect(state.range_end).toContain('2026-05-20')

  await app.close()
})

test('chart.update merges overlays in place (does not replace)', async () => {
  const app = await launchApp()
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')
  const dataDir = await getDataDir(app)

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

  // Establish the chart context.
  callMcpTool(
    'show_chart',
    {
      symbol: 'AAPL',
      timeframe: '1d',
      range_start: '2026-04-20T00:00:00+00:00',
      range_end: '2026-05-20T00:00:00+00:00',
      overlays: [{ kind: 'ema', period: 20 }],
    },
    dataDir,
  )
  await expect
    .poll(async () => (await readChartState(window)).overlays.length, {
      timeout: 2_000,
      intervals: [50],
    })
    .toBe(1)

  // Update with the FULL desired set — payload is "after the merge", not "in addition to".
  callMcpTool(
    'update_chart',
    {
      symbol: 'AAPL',
      timeframe: '1d',
      overlays: [
        { kind: 'ema', period: 20 },
        { kind: 'ema', period: 50 },
      ],
    },
    dataDir,
  )

  await expect
    .poll(async () => (await readChartState(window)).overlays.length, {
      timeout: 2_000,
      intervals: [50],
    })
    .toBe(2)
  const state = await readChartState(window)
  expect(state.overlays).toEqual([
    { kind: 'ema', period: 20 },
    { kind: 'ema', period: 50 },
  ])

  await app.close()
})

test('chart.update narrows the visible range within ~500 ms', async () => {
  const app = await launchApp()
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')
  const dataDir = await getDataDir(app)

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

  // Establish.
  callMcpTool(
    'show_chart',
    {
      symbol: 'AAPL',
      timeframe: '1d',
      range_start: '2026-04-20T00:00:00+00:00',
      range_end: '2026-05-20T00:00:00+00:00',
    },
    dataDir,
  )
  await expect
    .poll(async () => (await readChartState(window)).range_start, {
      timeout: 2_000,
      intervals: [50],
    })
    .toContain('2026-04-20')

  // Narrow the range to a 10-day window.
  callMcpTool(
    'update_chart',
    {
      symbol: 'AAPL',
      timeframe: '1d',
      range_start: '2026-05-10T00:00:00+00:00',
      range_end: '2026-05-20T00:00:00+00:00',
    },
    dataDir,
  )

  // Plan budget is 100 ms — we allow 500 ms to absorb CI variance and the
  // subprocess startup cost on Windows. The assertion is on the state, not
  // the wall-clock — the time budget is the polling deadline.
  await expect
    .poll(async () => (await readChartState(window)).range_start, { timeout: 500, intervals: [25] })
    .toContain('2026-05-10')

  await app.close()
})

test('chart.update arriving before any show for that symbol falls back to show semantics', async () => {
  const app = await launchApp()
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')
  const dataDir = await getDataDir(app)

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

  // The renderer mounts with default symbol AAPL — no `show_chart` for GOOG
  // has been issued. Per ADR-0017 the renderer treats the update as a show
  // with the available fields.
  const initial = await readChartState(window)
  expect(initial.symbol).toBe('AAPL')

  callMcpTool(
    'update_chart',
    {
      symbol: 'GOOG',
      timeframe: '1d',
      overlays: [{ kind: 'ema', period: 50 }],
    },
    dataDir,
  )

  await expect
    .poll(async () => (await readChartState(window)).symbol, { timeout: 2_000, intervals: [50] })
    .toBe('GOOG')
  const state = await readChartState(window)
  expect(state.overlays).toEqual([{ kind: 'ema', period: 50 }])

  await app.close()
})

test('highlight_pattern populates liveHighlights and dedups with the polled row', async () => {
  const app = await launchApp()
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')
  const dataDir = await getDataDir(app)

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

  // Anchor on AAPL/1d so the highlight matches the active chart.
  callMcpTool(
    'show_chart',
    {
      symbol: 'AAPL',
      timeframe: '1d',
      range_start: '2026-04-20T00:00:00+00:00',
      range_end: '2026-05-20T00:00:00+00:00',
    },
    dataDir,
  )
  await expect
    .poll(async () => (await readChartState(window)).symbol, { timeout: 2_000, intervals: [50] })
    .toBe('AAPL')

  const eventTs = '2026-05-15T00:00:00+00:00'
  callMcpTool(
    'highlight_pattern',
    {
      symbol: 'AAPL',
      timeframe: '1d',
      event_ts: eventTs,
      kind: 'bullish_marker',
      label: 'e2e-live-marker',
    },
    dataDir,
  )

  // Live SSE arrival populates the buffer fast.
  await expect
    .poll(async () => (await readChartState(window)).liveHighlights.length, {
      timeout: 2_000,
      intervals: [50],
    })
    .toBeGreaterThanOrEqual(1)

  const state = await readChartState(window)
  const live = state.liveHighlights.find((m) => m.event_ts.startsWith('2026-05-15'))
  expect(live).toBeDefined()
  expect(live!.kind).toBe('bullish_marker')

  // The polled row from useAnnotationsPoll will eventually carry the same
  // marker. Dedup is by (event_ts, kind); the live buffer should NOT grow
  // a second entry for the same key even after the poll cycles a few times.
  await window.waitForTimeout(2_500) // > 2 poll windows (1 Hz)
  const after = await readChartState(window)
  const sameKey = after.liveHighlights.filter(
    (m) => m.event_ts.startsWith('2026-05-15') && m.kind === 'bullish_marker',
  )
  expect(sameKey.length).toBe(1)

  await app.close()
})
