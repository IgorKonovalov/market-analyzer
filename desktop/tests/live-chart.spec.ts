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
  structured: Record<string, unknown> | null
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
  /** Plan 0030: candlestick bars currently set on the series. */
  barCount: number
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
    '                return {"isError": r.isError, "content": [str(c) for c in r.content], "structured": r.structuredContent}',
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
  // Plan 0027 phase 3: the always-on volume histogram is drawn on its own scale
  // from the bars the renderer already holds (no overlay needed to summon it).
  expect(render.seriesKinds).toContainEqual({ kind: 'volume' })

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

test('backfill_ohlcv shows the header spinner while filling, then clears with bars drawn', async () => {
  // Best-effort live test (Plan 0013 phase 4): drives the REAL backfill_ohlcv
  // tool against REAL Yahoo, like the other specs in this file. We branch on the
  // tool's `started` flag so the spinner assertion only runs when a backfill was
  // actually scheduled (a cold cache); on a warm cache (re-run on the same
  // machine) the tool is a no-op and we assert only that bars are drawn. The
  // deterministic spinner/refetch/toast logic is covered by the Jest
  // useBackfillState spec; this case proves the wiring lights up end-to-end.
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

  // A symbol+range the other specs don't touch, to maximise the chance the
  // cache is cold on a fresh machine.
  const symbol = 'IBM'
  const range_start = '2026-01-05T00:00:00+00:00'
  const range_end = '2026-03-06T00:00:00+00:00'

  callMcpTool('show_chart', { symbol, timeframe: '1d', range_start, range_end })
  await expect
    .poll(async () => (await readChartState(window)).symbol, { timeout: 2_000, intervals: [50] })
    .toBe(symbol)

  const ack = callMcpTool('backfill_ohlcv', {
    symbol,
    timeframe: '1d',
    start: range_start,
    end: range_end,
  })
  expect(ack.isError).toBe(false)

  const spinner = window.locator('[data-testid="ohlcv-backfill-spinner"]')
  if (ack.structured?.started === true) {
    // Backfill scheduled: spinner appears within the SSE round-trip budget,
    // then clears once Yahoo resolves.
    await expect.poll(() => spinner.count(), { timeout: 3_000, intervals: [50] }).toBeGreaterThan(0)
    await expect.poll(() => spinner.count(), { timeout: 12_000, intervals: [100] }).toBe(0)
  }

  // Either path ends with bars drawn — the loop the user reported broken.
  await expect
    .poll(async () => (await readChartRender(window)).seriesCount, {
      timeout: 12_000,
      intervals: [100],
    })
    .toBeGreaterThanOrEqual(1)

  await app.close()
})

// --------------------------------------------------------------------------- //
// Plan 0014 — agent-mode toggle + range-select gesture → get_pending_ui_events  //
// --------------------------------------------------------------------------- //

interface UiEventEnvelope {
  type: string
  version: number
  ts: string
  event_id: string
  payload: Record<string, unknown>
}

/** Drain the UI-event buffer via the MCP tool; returns the buffered envelopes. */
function drainUiEvents(): UiEventEnvelope[] {
  const result = callMcpTool('get_pending_ui_events', {})
  expect(result.isError).toBe(false)
  const list = (result.structured?.result ?? []) as UiEventEnvelope[]
  return list
}

async function setAgentMode(window: import('@playwright/test').Page, on: boolean): Promise<void> {
  const toggle = window.locator('[data-testid="agent-mode-toggle"]')
  await expect.poll(() => toggle.count(), { timeout: 10_000, intervals: [100] }).toBeGreaterThan(0)
  const checked = (await toggle.getAttribute('aria-checked')) === 'true'
  if (checked !== on) {
    await toggle.click()
    await expect
      .poll(async () => (await toggle.getAttribute('aria-checked')) === 'true', {
        timeout: 3_000,
        intervals: [50],
      })
      .toBe(on)
  }
}

async function dragAcrossChart(window: import('@playwright/test').Page): Promise<void> {
  const chart = window.locator('[data-testid="candlestick-chart"]')
  const box = await chart.boundingBox()
  if (!box) throw new Error('chart has no bounding box')
  const y = box.y + box.height / 2
  await window.mouse.move(box.x + box.width * 0.3, y)
  await window.mouse.down()
  await window.mouse.move(box.x + box.width * 0.65, y, { steps: 8 })
  await window.mouse.up()
}

/** Boot the renderer, then drive a `show_chart` so the candlestick series is
 * actually rendered (the default load renders nothing until a symbol is shown).
 * Mirrors the proven flow of the `chart.show via MCP` test above. */
async function showAaplAndWaitForBars(window: import('@playwright/test').Page): Promise<void> {
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
    .poll(async () => (await readChartRender(window)).seriesCount, {
      timeout: 15_000,
      intervals: [200],
    })
    .toBeGreaterThanOrEqual(1)
}

test('agent mode ON: a range-select drag surfaces one ui.range_selected to get_pending_ui_events', async () => {
  const app = await launchApp()
  const window = await app.firstWindow()
  await showAaplAndWaitForBars(window)

  await setAgentMode(window, true)
  // Drain the toggle event the PUT synthesised so only the gesture remains.
  drainUiEvents()

  // Enter select-range cursor mode (only rendered while agent mode is ON).
  await window.locator('[data-testid="select-range-toggle"]').click()
  await dragAcrossChart(window)

  // The POST is a fast HTTP round-trip into the buffer; poll briefly.
  let rangeEvents: UiEventEnvelope[] = []
  await expect
    .poll(
      () => {
        rangeEvents = drainUiEvents().filter((e) => e.type === 'ui.range_selected')
        return rangeEvents.length
      },
      { timeout: 5_000, intervals: [200] },
    )
    .toBe(1)

  const payload = rangeEvents[0].payload as { range_start: string; range_end: string }
  // Bar-precise mapping is owned by the deterministic unit gesture test; here we
  // assert the live wiring: a well-formed, ordered range reached the agent.
  expect(rangeEvents[0].version).toBe(1)
  expect(new Date(payload.range_start).getTime()).toBeLessThanOrEqual(
    new Date(payload.range_end).getTime(),
  )

  await app.close()
})

test('agent mode OFF: the same drag buffers no UI events', async () => {
  const app = await launchApp()
  const window = await app.firstWindow()
  await showAaplAndWaitForBars(window)

  await setAgentMode(window, false)
  // Drain anything (incl. the toggle-OFF event) so the buffer starts empty.
  drainUiEvents()

  // With agent mode OFF there is no select-range control; a drag just pans.
  await dragAcrossChart(window)

  // Give any (incorrect) POST time to land, then confirm nothing was buffered.
  await window.waitForTimeout(750)
  expect(drainUiEvents()).toEqual([])

  await app.close()
})

// --------------------------------------------------------------------------- //
// Plan 0030 — scroll to the left edge lazy-loads (prepends) older bars          //
// --------------------------------------------------------------------------- //

test('scrolling to the left edge prepends older bars (Plan 0030)', async () => {
  // Best-effort live test (mirrors the backfill_ohlcv spec above): it drives a
  // REAL `/ohlcv` older-chunk fetch against REAL Yahoo. The mount-time fit sits
  // at the left edge, so the trigger normally fires on show; a leftward drag is
  // belt-and-suspenders. On a fully warm cache that already covers all available
  // history the older fetch may add nothing, so the bar-count-increased
  // assertion is GATED on the loading affordance having actually appeared.
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

  // A narrow window on a long-lived daily symbol ⇒ plenty of older history.
  callMcpTool('show_chart', {
    symbol: 'AAPL',
    timeframe: '1d',
    range_start: '2026-04-01T00:00:00+00:00',
    range_end: '2026-05-01T00:00:00+00:00',
  })

  await expect
    .poll(async () => (await readChartRender(window)).seriesCount, {
      timeout: 15_000,
      intervals: [200],
    })
    .toBeGreaterThanOrEqual(1)
  const initialBarCount = (await readChartRender(window)).barCount

  // Nudge the viewport hard toward the left edge (drag content rightward to
  // reveal earlier time), in case the initial fit didn't already trip it.
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

  const loading = window.locator('[data-testid="ohlcv-history-loading"]')
  let sawAffordance = false
  try {
    await expect
      .poll(() => loading.count(), { timeout: 8_000, intervals: [100] })
      .toBeGreaterThan(0)
    sawAffordance = true
  } catch {
    sawAffordance = false
  }

  if (sawAffordance) {
    // The affordance clears once the older chunk resolves...
    await expect.poll(() => loading.count(), { timeout: 15_000, intervals: [200] }).toBe(0)
    // ...and the candlestick series grew (older bars were prepended).
    await expect
      .poll(async () => (await readChartRender(window)).barCount, {
        timeout: 5_000,
        intervals: [100],
      })
      .toBeGreaterThan(initialBarCount)
  }

  await app.close()
})
