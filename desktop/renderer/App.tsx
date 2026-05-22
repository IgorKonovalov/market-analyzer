/**
 * Two views: OHLCV chart (default) and MCP settings. Plan 0006 phase 5 added
 * the toggle.
 *
 * Plan 0007 phase 4 lifts the chart-context state (symbol/timeframe/range/
 * overlays/live-highlights) out of OhlcvView so the SSE event handlers can
 * mutate it without remounting the chart. The reducer + handlers live in
 * `renderer/handlers/chartHandlers.ts`; `useEventStream` is mounted at this
 * top level so the subscription survives view switches between Chart and
 * Settings.
 */
import { useEffect, useReducer, useState } from 'react'

import { chartReducer, initialChartState, DEFAULT_LOOKBACK_DAYS } from './handlers/chartHandlers'
import { useEventStream } from './hooks/useEventStream'
import styles from './App.module.css'
import type { Timeframe } from './components/SymbolPicker'
import { OhlcvView } from './views/OhlcvView'
import { SettingsView } from './views/SettingsView'

type View = 'chart' | 'settings'

/**
 * Test-only window-attached snapshot of the chart state. The Playwright
 * e2e in `tests/live-chart.spec.ts` asserts against this rather than
 * canvas pixels (per Plan 0007 phase 4's done-when, which calls out
 * `window.__test_chart_state__.overlays` explicitly). Contains no secrets
 * — bearer is held only in the api/client.ts module closure — so leaving
 * the hook attached in production is acceptable.
 */
declare global {
  interface Window {
    __test_chart_state__?: {
      symbol: string
      timeframe: string
      range_start: string
      range_end: string
      overlays: ReadonlyArray<{ kind: string; period?: number | null }>
      liveHighlights: ReadonlyArray<{ event_ts: string; kind: string; label?: string | null }>
    }
  }
}

export function App(): JSX.Element {
  const [view, setView] = useState<View>('chart')
  const [chartState, dispatch] = useReducer(chartReducer, undefined, () =>
    initialChartState(new Date().toISOString()),
  )

  useEventStream({
    onChartShow: (payload) => dispatch({ kind: 'event/chart.show', payload }),
    onChartUpdate: (payload) => dispatch({ kind: 'event/chart.update', payload }),
    onChartHighlight: (payload) => dispatch({ kind: 'event/chart.highlight', payload }),
    onRunCompleted: (payload) => {
      // Backtester has not shipped yet (Plan 0008) — no producer for this
      // envelope exists. When it does, a toast component lands here. For
      // now we log so the path is visible in the renderer console.
      console.info('[App] run.completed', payload)
    },
    onUpdateDropped: () => {
      console.warn('[App] chart.update_dropped — sidecar queue was full')
    },
  })

  // Mirror chart state onto a window-attached snapshot for the e2e test
  // hook. Re-runs on every reducer transition so Playwright sees the live
  // shape, not a stale capture.
  useEffect(() => {
    window.__test_chart_state__ = {
      symbol: chartState.symbol,
      timeframe: chartState.timeframe,
      range_start: chartState.range_start,
      range_end: chartState.range_end,
      overlays: chartState.overlays,
      liveHighlights: chartState.liveHighlights,
    }
  }, [chartState])

  const onSymbolChange = (symbol: string): void => dispatch({ kind: 'ui/set-symbol', symbol })
  const onTimeframeChange = (timeframe: Timeframe): void =>
    dispatch({ kind: 'ui/set-timeframe', timeframe })
  const onRefresh = (): void =>
    dispatch({
      kind: 'ui/refresh',
      nowIso: new Date().toISOString(),
      lookbackDays: DEFAULT_LOOKBACK_DAYS,
    })

  return (
    <main className="appShell">
      <header className="appHeader">
        <h1>market-analyser</h1>
        <nav className={styles.nav} aria-label="Primary">
          <button
            type="button"
            className={styles.tab}
            aria-current={view === 'chart' ? 'page' : undefined}
            onClick={() => setView('chart')}
            data-testid="nav-chart"
          >
            Chart
          </button>
          <button
            type="button"
            className={styles.tab}
            aria-current={view === 'settings' ? 'page' : undefined}
            onClick={() => setView('settings')}
            data-testid="nav-settings"
          >
            Settings
          </button>
        </nav>
      </header>
      {view === 'chart' ? (
        <OhlcvView
          symbol={chartState.symbol}
          timeframe={chartState.timeframe}
          range_start={chartState.range_start}
          range_end={chartState.range_end}
          liveHighlights={chartState.liveHighlights}
          overlays={chartState.overlays}
          onSymbolChange={onSymbolChange}
          onTimeframeChange={onTimeframeChange}
          onRefresh={onRefresh}
        />
      ) : (
        <SettingsView />
      )}
    </main>
  )
}
