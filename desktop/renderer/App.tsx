/**
 * Four views: OHLCV chart (default), MCP settings, Recent backtests list,
 * Backtest result. Plan 0008 phase 5 added the backtest pair.
 *
 * Plan 0007 phase 4 lifts the chart-context state (symbol/timeframe/range/
 * overlays/live-highlights) out of OhlcvView so the SSE event handlers can
 * mutate it without remounting the chart. The reducer + handlers live in
 * `renderer/handlers/chartHandlers.ts`; `useEventStream` is mounted at this
 * top level so the subscription survives view switches.
 *
 * Plan 0008 phase 5 routes `run.completed v1` envelopes (only when
 * `payload.kind === 'backtest'`) to:
 *   - a small in-memory bus (`runCompletedBus`) that `useBacktestResult`
 *     subscribes to from anywhere in the tree;
 *   - `selectedRunId` + `view = 'backtest'` so the renderer auto-swaps to
 *     the new result — matches the chosen UX (most-recent-wins) and the
 *     Playwright e2e expectation that BacktestView is visible within 3 s
 *     of the MCP call.
 */
import { useEffect, useReducer, useState } from 'react'

import { notifyBackfill } from './handlers/backfillBus'
import { chartReducer, initialChartState, DEFAULT_LOOKBACK_DAYS } from './handlers/chartHandlers'
import { notifyRunCompleted } from './handlers/runCompletedBus'
import { useBacktestResult } from './hooks/useBacktestResult'
import { useEventStream } from './hooks/useEventStream'
import styles from './App.module.css'
import { ThemeToggle } from './components/ThemeToggle'
import type { Timeframe } from './lib/timeframes'
import type { SignalEvaluation } from './types/events'
import { BacktestView } from './views/BacktestView'
import { LiveSignalView } from './views/LiveSignalView'
import { NewsView } from './views/NewsView'
import { OhlcvView } from './views/OhlcvView'
import { RecentBacktestsView } from './views/RecentBacktestsView'
import { SettingsView } from './views/SettingsView'

type View = 'chart' | 'news' | 'signals' | 'settings' | 'backtest' | 'recent-backtests'

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
    /** Plan 0008 phase 5 e2e seam — publishes a synthetic `run.completed v1`
     * payload onto the renderer-internal bus so Playwright can drive the
     * auto-route path without standing up a stub SSE producer. No secrets;
     * no production side effects (the same publisher is invoked by the
     * `useEventStream` consumer in `onRunCompleted`). */
    __test_publish_run_completed__?: (payload: {
      kind: 'backtest' | 'analysis' | 'defi'
      run_id: string
      artifact_path: string
    }) => void
  }
}

export function App(): JSX.Element {
  const [view, setView] = useState<View>('chart')
  const [chartState, dispatch] = useReducer(chartReducer, undefined, () =>
    initialChartState(new Date().toISOString()),
  )
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [recentListRefresh, setRecentListRefresh] = useState(0)
  // Latest live-signal evaluation (Plan 0026). Reactive-only: the panel reflects
  // whatever the agent last evaluated via `signal.evaluated v1`. No auto-switch —
  // the user navigates to the Signals tab; the most-recent evaluation persists.
  const [latestEvaluation, setLatestEvaluation] = useState<SignalEvaluation | null>(null)

  const backtestState = useBacktestResult({ runId: selectedRunId })

  // Single handler so the SSE path and the e2e test seam share semantics.
  const handleRunCompleted = (payload: {
    kind: 'backtest' | 'analysis' | 'defi'
    run_id: string
    artifact_path: string
  }): void => {
    notifyRunCompleted(payload)
    if (payload.kind === 'backtest') {
      setSelectedRunId(payload.run_id)
      setView('backtest')
      // Bump so the next time the user opens "Recent backtests" the list
      // includes the just-completed run. (The list is bounded; the
      // refetch is cheap.)
      setRecentListRefresh((n) => n + 1)
    } else {
      console.info('[App] run.completed (non-backtest)', payload)
    }
  }

  useEventStream({
    onChartShow: (payload) => dispatch({ kind: 'event/chart.show', payload }),
    onChartUpdate: (payload) => dispatch({ kind: 'event/chart.update', payload }),
    onChartHighlight: (payload) => dispatch({ kind: 'event/chart.highlight', payload }),
    onRunCompleted: handleRunCompleted,
    onSignalEvaluated: (payload) => setLatestEvaluation(payload.evaluation),
    onOhlcvBackfillStarted: (payload) => notifyBackfill({ kind: 'started', payload }),
    onOhlcvBackfilled: (payload) => notifyBackfill({ kind: 'backfilled', payload }),
    onOhlcvBackfillFailed: (payload) => notifyBackfill({ kind: 'failed', payload }),
    onUpdateDropped: () => {
      console.warn('[App] chart.update_dropped — sidecar queue was full')
    },
  })

  // Expose the same handler under a window-attached seam so the Playwright
  // e2e can drive the auto-route path without depending on a real SSE flush
  // through the sidecar (which would require either a stub event producer
  // or running an actual backtest against cached bars).
  useEffect(() => {
    window.__test_publish_run_completed__ = handleRunCompleted
    return () => {
      delete window.__test_publish_run_completed__
    }
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

  const onSelectRun = (runId: string): void => {
    setSelectedRunId(runId)
    setView('backtest')
  }
  const onBackToRecent = (): void => setView('recent-backtests')

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
            aria-current={view === 'recent-backtests' || view === 'backtest' ? 'page' : undefined}
            onClick={() => setView('recent-backtests')}
            data-testid="nav-backtests"
          >
            Backtests
          </button>
          <button
            type="button"
            className={styles.tab}
            aria-current={view === 'signals' ? 'page' : undefined}
            onClick={() => setView('signals')}
            data-testid="nav-signals"
          >
            Signals
          </button>
          <button
            type="button"
            className={styles.tab}
            aria-current={view === 'news' ? 'page' : undefined}
            onClick={() => setView('news')}
            data-testid="nav-news"
          >
            News
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
        <ThemeToggle />
      </header>
      {view === 'chart' && (
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
      )}
      {view === 'signals' && <LiveSignalView evaluation={latestEvaluation} />}
      {view === 'news' && <NewsView />}
      {view === 'settings' && <SettingsView />}
      {view === 'recent-backtests' && (
        <RecentBacktestsView onSelect={onSelectRun} refreshKey={recentListRefresh} />
      )}
      {view === 'backtest' && <BacktestPanel state={backtestState} onBack={onBackToRecent} />}
    </main>
  )
}

interface BacktestPanelProps {
  state: ReturnType<typeof useBacktestResult>
  onBack: () => void
}

/** Routes the hook's four states to the right surface. Idle is unreachable
 * via normal navigation (the parent only flips to `view='backtest'` after
 * setting a runId or receiving an envelope), but is rendered defensively. */
function BacktestPanel({ state, onBack }: BacktestPanelProps): JSX.Element {
  if (state.status === 'ready') {
    return <BacktestView result={state.result} onBack={onBack} />
  }
  if (state.status === 'loading') {
    return (
      <section className={styles.statusPanel} role="status" data-testid="backtest-loading">
        Loading backtest result…
      </section>
    )
  }
  if (state.status === 'error') {
    return (
      <section className={styles.statusPanel} role="alert" data-testid="backtest-error">
        <p>Failed to load backtest result: {state.error.message}</p>
        <button type="button" onClick={onBack}>
          Back to Recent backtests
        </button>
      </section>
    )
  }
  return (
    <section className={styles.statusPanel} role="status" data-testid="backtest-idle">
      <p>No backtest selected. Open Recent backtests to pick one.</p>
      <button type="button" onClick={onBack}>
        Recent backtests
      </button>
    </section>
  )
}
