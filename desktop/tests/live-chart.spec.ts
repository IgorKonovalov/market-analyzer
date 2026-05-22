/**
 * Plan 0007 phase 4 (refreshed at phase 4.5). Drives the running sidecar's
 * MCP surface from a Python subprocess (matches `tests/ohlcv-view.spec.ts`'s
 * pattern for `insertAnnotation`) and asserts the renderer's chart state
 * snapshot AND the live render hook both react within the SSE round-trip
 * budget.
 *
 * Two test hooks on `window`:
 *   - `__test_chart_state__` — the reducer's snapshot of what the chart
 *     SHOULD show. Used for the data-shape claims (symbol, range, live
 *     highlights, dedup invariants).
 *   - `__test_chart_render__` — what `lightweight-charts` ACTUALLY drew on
 *     the chart instance. Used for the overlay-rendering claim. The
 *     reducer can record an overlay, but if `OhlcvView` doesn't pass it to
 *     the chart and the chart doesn't add a line series, the user sees
 *     nothing — and the reducer-only assertion can pass with no chart
 *     change (the gap defect 3 documented before phase 4.5 closed it).
 *
 * Phase 4.1 (ADR-0020) made Electron and the Python sidecar agree on the
 * canonical data directory by construction, so this spec no longer needs
 * to inject the data-dir env var into the subprocess — the subprocess
 * reads `default_app_data_dir()` and finds the same lockfile Electron
 * just wrote. If a future change reintroduces a dataDir divergence, this
 * spec will fail at the first MCP tool call, surfacing the regression.
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

interface ChartRenderSnapshot {
  seriesCount: number
  seriesKinds: Array<{ kind: string; period?: number | null }>
}

function callMcpTool(tool: string, args: Record<string, unknown>): ToolResult {
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
    '                return {"isError": r.isError, "content": [str(c) for c in r.content]}',
    'print(json.dumps(asyncio.run(run())))',
  ].join('\n')

  const result = spawnSync('uv', ['run', '--no-sync', 'python', '-c', script], {
    cwd: REPO_ROOT,
    input: JSON.stringify({ tool, args }),
    encoding: 'utf-8',
    shell: false,
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

async function readChartRender(
  window: import('@playwright/test').Page,
): Promise<ChartRenderSnapshot> {
  const snapshot = await window.evaluate(
    () => (globalThis as { __test_chart_render__?: unknown }).__test_chart_render__,
  )
  if (!snapshot) throw new Error('__test_chart_render__ not exposed on window')
  return snapshot as ChartRenderSnapshot
}

async function launchApp(): Promise<ElectronApplication> {
  return electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
  })
}

test('chart.show via MCP switches the renderer to the requested symbol and overlays', async () => {
  const app = await launchApp()
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')

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

  const ack = callMcpTool('show_chart', {
    symbol: 'MSFT',
    timeframe: '1d',
    range_start: '2026-04-20T00:00:00+00:00',
    range_end: '2026-05-20T00:00:00+00:00',
    overlays: [{ kind: 'ema', period: 20 }],
  })
  expect(ack.isError).toBe(false)

  await expect
    .poll(async () => (await readChartState(window)).symbol, { timeout: 2_000, intervals: [50] })
    .toBe('MSFT')

  const state = await readChartState(window)
  expect(state.timeframe).toBe('1d')
  // Reducer-side sanity check — the SSE event landed and was applied.
  expect(state.overlays).toEqual([{ kind: 'ema', period: 20 }])
  expect(state.range_start).toContain('2026-04-20')
  expect(state.range_end).toContain('2026-05-20')

  // Rendering claim (Plan 0007 phase 4.5): the chart actually drew an EMA
  // line series, not just recorded one in the reducer. Wait for the bars
  // to load and the chart to render before reading the hook.
  await expect
    .poll(async () => (await readChartRender(window)).seriesCount, {
      timeout: 5_000,
      intervals: [100],
    })
    .toBeGreaterThanOrEqual(2)
  const render = await readChartRender(window)
  expect(render.seriesKinds).toContainEqual({ kind: 'candlestick' })
  expect(render.seriesKinds).toContainEqual({ kind: 'ema', period: 20 })

  await app.close()
})

test('chart.update merges overlays in place (does not replace)', async () => {
  const app = await launchApp()
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')

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

  callMcpTool('show_chart', {
    symbol: 'AAPL',
    timeframe: '1d',
    range_start: '2026-04-20T00:00:00+00:00',
    range_end: '2026-05-20T00:00:00+00:00',
    overlays: [{ kind: 'ema', period: 20 }],
  })
  await expect
    .poll(async () => (await readChartState(window)).overlays.length, {
      timeout: 2_000,
      intervals: [50],
    })
    .toBe(1)

  callMcpTool('update_chart', {
    symbol: 'AAPL',
    timeframe: '1d',
    overlays: [
      { kind: 'ema', period: 20 },
      { kind: 'ema', period: 50 },
    ],
  })

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

  // Phase 4.5 rendering claim: the chart has TWO EMA line series drawn.
  // A reducer-only check would have passed even when defect 3 was live; the
  // render-side hook is the structural defence.
  await expect
    .poll(
      async () =>
        (await readChartRender(window)).seriesKinds.filter((s) => s.kind === 'ema').length,
      { timeout: 5_000, intervals: [100] },
    )
    .toBe(2)

  await app.close()
})

test('chart.update narrows the visible range within ~500 ms', async () => {
  const app = await launchApp()
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')

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

  callMcpTool('show_chart', {
    symbol: 'AAPL',
    timeframe: '1d',
    range_start: '2026-04-20T00:00:00+00:00',
    range_end: '2026-05-20T00:00:00+00:00',
  })
  await expect
    .poll(async () => (await readChartState(window)).range_start, {
      timeout: 2_000,
      intervals: [50],
    })
    .toContain('2026-04-20')

  // Narrow the range to a 10-day window.
  callMcpTool('update_chart', {
    symbol: 'AAPL',
    timeframe: '1d',
    range_start: '2026-05-10T00:00:00+00:00',
    range_end: '2026-05-20T00:00:00+00:00',
  })

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

  callMcpTool('update_chart', {
    symbol: 'GOOG',
    timeframe: '1d',
    overlays: [{ kind: 'ema', period: 50 }],
  })

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
  callMcpTool('show_chart', {
    symbol: 'AAPL',
    timeframe: '1d',
    range_start: '2026-04-20T00:00:00+00:00',
    range_end: '2026-05-20T00:00:00+00:00',
  })
  await expect
    .poll(async () => (await readChartState(window)).symbol, { timeout: 2_000, intervals: [50] })
    .toBe('AAPL')

  const eventTs = '2026-05-15T00:00:00+00:00'
  callMcpTool('highlight_pattern', {
    symbol: 'AAPL',
    timeframe: '1d',
    event_ts: eventTs,
    kind: 'bullish_marker',
    label: 'e2e-live-marker',
    agent_id: 'e2e',
  })

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
