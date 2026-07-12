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

import { notifyAlert } from './handlers/alertBus'
import { notifyBackfill } from './handlers/backfillBus'
import { chartReducer, initialChartState, DEFAULT_LOOKBACK_DAYS } from './handlers/chartHandlers'
import { notifyRunCompleted } from './handlers/runCompletedBus'
import { useBacktestResult } from './hooks/useBacktestResult'
import { useEventStream } from './hooks/useEventStream'
import { useLocale } from './hooks/useLocalePref'
import styles from './App.module.css'
import { AlertToaster } from './components/AlertToaster'
import { ThemeToggle } from './components/ThemeToggle'
import { t } from './lib/i18n'
import type { Timeframe } from './lib/timeframes'
import type {
  MultiHorizonForecastResult,
  PredictionScreenCompletedPayloadV1,
  Recommendation,
  RegimeForecast,
  SignalEvaluation,
  TechnicalRead,
  VolatilityForecast,
} from './types/events'
import { AlertsView } from './views/AlertsView'
import { BacktestView } from './views/BacktestView'
import { ConvergenceView } from './views/ConvergenceView'
import { DefiPnlView } from './views/DefiPnlView'
import { ForecastView } from './views/ForecastView'
import { LiveSignalView } from './views/LiveSignalView'
import { NewsView } from './views/NewsView'
import { OhlcvView } from './views/OhlcvView'
import { RecentBacktestsView } from './views/RecentBacktestsView'
import { RecommendationsView } from './views/RecommendationsView'
import { SettingsView } from './views/SettingsView'
import { TechnicalReadView } from './views/TechnicalReadView'
import { TrackRecordView } from './views/TrackRecordView'

type View =
  | 'chart'
  | 'news'
  | 'signals'
  | 'recommendations'
  | 'technical-read'
  | 'track-record'
  | 'forecast'
  | 'convergence'
  | 'defi'
  | 'settings'
  | 'backtest'
  | 'recent-backtests'
  | 'alerts'

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
  // Subscribe to the locale at the root so any `setLocale` re-renders the whole
  // tree and every `t()`-keyed surface flips on the spot (Plan 0069 phase 3).
  const locale = useLocale()
  const [view, setView] = useState<View>('chart')
  const [chartState, dispatch] = useReducer(chartReducer, undefined, () =>
    initialChartState(new Date().toISOString()),
  )
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [recentListRefresh, setRecentListRefresh] = useState(0)
  // Track-record refetch trigger (Plan 0080 phase 5). Bumped on each
  // `recommendation.scored` event so the TrackRecordView refetches the
  // authoritative `GET /track_record` aggregate — deliberately WITHOUT switching
  // the view (a scored fact must not grab the screen, the ADR-0075/ADR-0029
  // quiet-report posture, same as the Recommendations panel); the user opens the
  // Track-record tab when they want to read it.
  const [trackRecordRefresh, setTrackRecordRefresh] = useState(0)
  // Latest live-signal evaluation (Plan 0026). Reactive-only: the panel reflects
  // whatever the agent last evaluated via `signal.evaluated v1`. No auto-switch —
  // the user navigates to the Signals tab; the most-recent evaluation persists.
  const [latestEvaluation, setLatestEvaluation] = useState<SignalEvaluation | null>(null)
  // Latest advisory recommendation (Plan 0039). Same reactive-only posture as
  // the signals panel — and deliberately NO auto-switch: an advisory call must
  // not grab the screen (ADR-0029's quiet-advice framing); the user opens the
  // Recommendations tab when they want to read it.
  const [latestRecommendation, setLatestRecommendation] = useState<Recommendation | null>(null)
  // Latest multi-horizon forecast (Plan 0037). Same reactive-only posture, and
  // deliberately NO auto-switch: a probability must not grab the screen
  // (ADR-0030's honest-uncertainty framing); the user opens the Forecast tab.
  const [latestForecast, setLatestForecast] = useState<MultiHorizonForecastResult | null>(null)
  // Latest volatility + regime forecasts (Plan 0077 phase 6). Same reactive-only,
  // NO-auto-switch posture as the direction forecast — a non-directional forecast
  // must not grab the screen either (ADR-0037); the user opens the Forecast tab.
  const [latestVolatility, setLatestVolatility] = useState<VolatilityForecast | null>(null)
  const [latestRegime, setLatestRegime] = useState<RegimeForecast | null>(null)
  // Latest single-indicator technical read (Plan 0074 phase 3, ADR-0068). Same
  // reactive-only, NO-auto-switch posture as the recommendation panel — the lesser
  // advisory tier is a thin read and must not grab the screen; the user opens the
  // Technical read tab when they want it.
  const [latestTechnicalRead, setLatestTechnicalRead] = useState<TechnicalRead | null>(null)
  // Latest convergence screen (Plan 0078 phase 3, ADR-0041/0029). Same
  // reactive-only, NO-auto-switch posture as the recommendation/forecast panels —
  // an opportunity must not grab the screen; the user opens the Convergence tab
  // when they want to read it.
  const [latestScreen, setLatestScreen] = useState<PredictionScreenCompletedPayloadV1 | null>(null)

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
    onChartTrendlines: (payload) => dispatch({ kind: 'event/chart.trendlines', payload }),
    onRunCompleted: handleRunCompleted,
    onSignalEvaluated: (payload) => setLatestEvaluation(payload.evaluation),
    onRecommendationCompleted: (payload) => setLatestRecommendation(payload.recommendation),
    // Plan 0080 phase 5: a scored call refetches the track record — no auto-switch.
    onRecommendationScored: () => setTrackRecordRefresh((n) => n + 1),
    onForecastCompleted: (payload) => setLatestForecast(payload.forecast),
    onVolatilityForecastCompleted: (payload) => setLatestVolatility(payload.forecast),
    onRegimeForecastCompleted: (payload) => setLatestRegime(payload.forecast),
    // Plan 0074 phase 3: the lesser advisory tier — reactive-only, no auto-switch.
    onTechnicalReadCompleted: (payload) => setLatestTechnicalRead(payload.read),
    // Plan 0078 phase 3: convergence opportunities — reactive-only, no auto-switch
    // (an opportunity must not grab the screen, the ADR-0029/0041 posture).
    onPredictionScreenCompleted: (payload) => setLatestScreen(payload),
    onOhlcvBackfillStarted: (payload) => notifyBackfill({ kind: 'started', payload }),
    onOhlcvBackfilled: (payload) => notifyBackfill({ kind: 'backfilled', payload }),
    onOhlcvBackfillFailed: (payload) => notifyBackfill({ kind: 'failed', payload }),
    // Plan 0060: validated alert payloads fan out on the alertBus — the
    // AlertToaster (any view) and AlertsView's live-prepend both subscribe.
    onAlertTriggered: (payload) => notifyAlert(payload),
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
    <main className="appShell" lang={locale}>
      <header className="appHeader">
        <h1>market-analyser</h1>
        <nav className={styles.nav} aria-label={t('app.nav.primaryLabel')}>
          <button
            type="button"
            className={styles.tab}
            aria-current={view === 'chart' ? 'page' : undefined}
            onClick={() => setView('chart')}
            data-testid="nav-chart"
          >
            {t('app.nav.chart')}
          </button>
          <button
            type="button"
            className={styles.tab}
            aria-current={view === 'recent-backtests' || view === 'backtest' ? 'page' : undefined}
            onClick={() => setView('recent-backtests')}
            data-testid="nav-backtests"
          >
            {t('app.nav.backtests')}
          </button>
          <button
            type="button"
            className={styles.tab}
            aria-current={view === 'signals' ? 'page' : undefined}
            onClick={() => setView('signals')}
            data-testid="nav-signals"
          >
            {t('app.nav.signals')}
          </button>
          <button
            type="button"
            className={styles.tab}
            aria-current={view === 'recommendations' ? 'page' : undefined}
            onClick={() => setView('recommendations')}
            data-testid="nav-recommendations"
          >
            {t('app.nav.recommendations')}
          </button>
          <button
            type="button"
            className={styles.tab}
            aria-current={view === 'technical-read' ? 'page' : undefined}
            onClick={() => setView('technical-read')}
            data-testid="nav-technical-read"
          >
            {t('app.nav.technicalRead')}
          </button>
          <button
            type="button"
            className={styles.tab}
            aria-current={view === 'track-record' ? 'page' : undefined}
            onClick={() => setView('track-record')}
            data-testid="nav-track-record"
          >
            {t('app.nav.trackRecord')}
          </button>
          <button
            type="button"
            className={styles.tab}
            aria-current={view === 'forecast' ? 'page' : undefined}
            onClick={() => setView('forecast')}
            data-testid="nav-forecast"
          >
            {t('app.nav.forecast')}
          </button>
          <button
            type="button"
            className={styles.tab}
            aria-current={view === 'convergence' ? 'page' : undefined}
            onClick={() => setView('convergence')}
            data-testid="nav-convergence"
          >
            {t('app.nav.convergence')}
          </button>
          <button
            type="button"
            className={styles.tab}
            aria-current={view === 'defi' ? 'page' : undefined}
            onClick={() => setView('defi')}
            data-testid="nav-defi"
          >
            {t('app.nav.defi')}
          </button>
          <button
            type="button"
            className={styles.tab}
            aria-current={view === 'news' ? 'page' : undefined}
            onClick={() => setView('news')}
            data-testid="nav-news"
          >
            {t('app.nav.news')}
          </button>
          <button
            type="button"
            className={styles.tab}
            aria-current={view === 'alerts' ? 'page' : undefined}
            onClick={() => setView('alerts')}
            data-testid="nav-alerts"
          >
            {t('app.nav.alerts')}
          </button>
          <button
            type="button"
            className={styles.tab}
            aria-current={view === 'settings' ? 'page' : undefined}
            onClick={() => setView('settings')}
            data-testid="nav-settings"
          >
            {t('app.nav.settings')}
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
          trendlines={chartState.trendlines}
          onSymbolChange={onSymbolChange}
          onTimeframeChange={onTimeframeChange}
          onRefresh={onRefresh}
        />
      )}
      {view === 'signals' && <LiveSignalView evaluation={latestEvaluation} />}
      {view === 'recommendations' && <RecommendationsView recommendation={latestRecommendation} />}
      {view === 'technical-read' && <TechnicalReadView read={latestTechnicalRead} />}
      {view === 'track-record' && <TrackRecordView refreshKey={trackRecordRefresh} />}
      {view === 'forecast' && (
        <ForecastView
          forecast={latestForecast}
          volatility={latestVolatility}
          regime={latestRegime}
        />
      )}
      {view === 'convergence' && <ConvergenceView screen={latestScreen} />}
      {view === 'defi' && <DefiPnlView />}
      {view === 'alerts' && <AlertsView />}
      {view === 'news' && <NewsView />}
      {view === 'settings' && <SettingsView />}
      {view === 'recent-backtests' && (
        <RecentBacktestsView onSelect={onSelectRun} refreshKey={recentListRefresh} />
      )}
      {view === 'backtest' && <BacktestPanel state={backtestState} onBack={onBackToRecent} />}
      <AlertToaster />
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
        {t('app.backtest.loading')}
      </section>
    )
  }
  if (state.status === 'error') {
    return (
      <section className={styles.statusPanel} role="alert" data-testid="backtest-error">
        <p>
          {t('app.backtest.loadError')} {state.error.message}
        </p>
        <button type="button" onClick={onBack}>
          {t('app.backtest.backToRecent')}
        </button>
      </section>
    )
  }
  return (
    <section className={styles.statusPanel} role="status" data-testid="backtest-idle">
      <p>{t('app.backtest.noneSelected')}</p>
      <button type="button" onClick={onBack}>
        {t('app.backtest.recentBacktests')}
      </button>
    </section>
  )
}
