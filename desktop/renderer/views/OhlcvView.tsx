/**
 * The chart route. **Controlled** — the parent (App.tsx) owns symbol,
 * timeframe, range, overlays, and the live-highlights buffer, so SSE
 * envelopes from `useEventStream` can mutate the chart context without
 * remounting (Plan 0007 phase 4).
 *
 * The view still owns the four async states (loading / error / empty /
 * populated) and the Refresh button — those are local UI concerns.
 *
 * Live highlights and polled annotations are merged here and deduped on
 * `(event_ts, kind)` so a live `chart.highlight` event followed by the
 * polled annotation row ~1 s later does not produce a duplicate marker.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { AgentModeToggle } from '../components/AgentModeToggle'
import { CandlestickChart } from '../components/CandlestickChart'
import { SymbolPicker } from '../components/SymbolPicker'
import { Toast } from '../components/Toast'
import { t } from '../lib/i18n'
import type { ChartMarker } from '../lib/markers'
import type { Timeframe } from '../lib/timeframes'
import { useAgentMode } from '../hooks/useAgentMode'
import { useAnnotationsPoll } from '../hooks/useAnnotationsPoll'
import { useBackfillState } from '../hooks/useBackfillState'
import { useOhlcvHistory } from '../hooks/useOhlcvHistory'
import { useQuotePoll } from '../hooks/useQuotePoll'
import type { Divergence, Marker, OverlaySpec, TrendlineSpec } from '../types/events'
import type { Annotation } from '../types/sidecar/annotation'
import type { QuoteResponse } from '../types/sidecar/quote-response'
import styles from './OhlcvView.module.css'

export interface OhlcvViewProps {
  symbol: string
  timeframe: Timeframe
  /** ISO 8601 UTC, inclusive. */
  range_start: string
  /** ISO 8601 UTC, inclusive. */
  range_end: string
  liveHighlights: Marker[]
  /** Plan 0007 phase 4.5: overlay specs to render on top of the candlestick
   * series. The chart renders supported kinds (ema, sma) and logs-and-skips
   * unsupported ones (rsi, macd, bbands). */
  overlays: ReadonlyArray<OverlaySpec>
  /** Plan 0052 phase 4 (ADR-0049): sloped trendlines (necklines, triangle/
   * wedge bounds) the agent pushed via `chart.show`/`chart.update`. */
  trendlines?: ReadonlyArray<TrendlineSpec>
  /** Plan 0091 phase 9 (ADR-0090): price↔oscillator divergences the agent surfaced
   * via `chart.divergences`, drawn as two segments across the price + oscillator panes. */
  divergences?: ReadonlyArray<Divergence>
  onSymbolChange: (symbol: string) => void
  onTimeframeChange: (timeframe: Timeframe) => void
  onRefresh: () => void
}

export function OhlcvView({
  symbol,
  timeframe,
  range_start,
  range_end,
  liveHighlights,
  overlays,
  trendlines,
  divergences,
  onSymbolChange,
  onTimeframeChange,
  onRefresh,
}: OhlcvViewProps): JSX.Element {
  const start = useMemo(() => new Date(range_start), [range_start])
  const end = useMemo(() => new Date(range_end), [range_end])

  const {
    bars,
    isLoading,
    isRefetching,
    error,
    historyClampedDays,
    refetch,
    loadOlder,
    isLoadingOlder,
    olderError,
    reachedStart,
  } = useOhlcvHistory({ symbol, timeframe, start, end })
  // Lazy paging prepends bars older than the initial window; widen the
  // annotation poll to the buffer's earliest so markers cover prepended bars
  // (Plan 0030 phase 1). End stays the prop window; we never page right.
  const annStart = useMemo(
    () => (bars && bars.length > 0 ? new Date(bars[0].event_ts) : start),
    [bars, start],
  )
  const { annotations } = useAnnotationsPoll({ symbol, timeframe, start: annStart, end })
  const { isBackfilling, error: backfillError } = useBackfillState({ symbol, timeframe, refetch })
  const { enabled: agentModeEnabled, setEnabled: setAgentMode } = useAgentMode()
  // Live, symbol-level price — keyed on symbol only, so it is independent of the
  // selected timeframe and never derives from the last bar's close (Plan 0047).
  // `quoteError` is set while the latest poll is failing (the hook keeps the
  // last-known quote on screen) so the header can flag a possibly-stale price.
  const { quote, error: quoteError } = useQuotePoll({ symbol })

  // A fresh backfill failure re-shows the toast even if a prior one was dismissed.
  const [toastDismissed, setToastDismissed] = useState(false)
  useEffect(() => {
    if (backfillError) setToastDismissed(false)
  }, [backfillError])
  const showToast = backfillError !== null && !toastDismissed

  // Refresh = advance the window end to "now" (via the parent) AND force a
  // genuine reload of the series. Window-advance alone often takes the cheap
  // overlapping-edge path, which is a no-op when there are no newer bars (market
  // closed, same session) — so the button felt dead. The `refetch()` makes every
  // click a real, observable load that drives the in-flight/confirmation states.
  const handleRefresh = useCallback(() => {
    onRefresh()
    refetch()
  }, [onRefresh, refetch])

  // Brief "Updated ✓" confirmation on a successful refresh. A cached reload can
  // settle faster than the eye catches the spinner, so a short post-completion
  // flash is what actually makes the action feel like it did something.
  const [justRefreshed, setJustRefreshed] = useState(false)
  const wasRefetchingRef = useRef(false)
  useEffect(() => {
    const was = wasRefetchingRef.current
    wasRefetchingRef.current = isRefetching
    if (was && !isRefetching && !error) {
      setJustRefreshed(true)
      const timer = setTimeout(() => setJustRefreshed(false), 1400)
      return () => clearTimeout(timer)
    }
    return undefined
  }, [isRefetching, error])

  const mergedAnnotations = useMemo(
    () => mergePolledAndLive(annotations, liveHighlights, symbol, timeframe),
    [annotations, liveHighlights, symbol, timeframe],
  )

  return (
    <section className={styles.root} aria-label={t('ohlcv.viewLabel', { symbol, timeframe })}>
      <header className={styles.toolbar}>
        <SymbolPicker
          symbol={symbol}
          timeframe={timeframe}
          onSymbolChange={onSymbolChange}
          onTimeframeChange={onTimeframeChange}
          disabled={isLoading}
        />
        <PriceHeader symbol={symbol} quote={quote} disconnected={quoteError !== null} />
        <button
          type="button"
          className={styles.refresh}
          onClick={handleRefresh}
          disabled={isLoading || isRefetching}
          // Stable accessible name across the visual states below, so assistive
          // tech (and the e2e role lookup) always see the button as "Refresh".
          // `aria-busy` carries the in-flight state to screen readers.
          aria-label={t('ohlcv.refresh')}
          aria-busy={isRefetching}
          data-testid="ohlcv-refresh"
          data-state={isRefetching ? 'refreshing' : justRefreshed ? 'updated' : 'idle'}
        >
          {isRefetching ? (
            <>
              <span className={styles.spinnerDot} aria-hidden="true" />
              {t('ohlcv.refreshing')}
            </>
          ) : justRefreshed ? (
            t('ohlcv.updated')
          ) : (
            t('ohlcv.refresh')
          )}
        </button>
        {isBackfilling && (
          <span
            className={styles.backfillSpinner}
            role="status"
            data-testid="ohlcv-backfill-spinner"
            aria-label={t('ohlcv.backfillingLabel', { symbol, timeframe })}
          >
            <span className={styles.spinnerDot} aria-hidden="true" />
            {t('ohlcv.backfilling')}
          </span>
        )}
        <AgentModeToggle
          enabled={agentModeEnabled}
          setEnabled={setAgentMode}
          disabled={isLoading}
        />
      </header>

      <div className={styles.body}>
        {isLoading && (
          <div className={styles.skeleton} role="status" aria-label={t('ohlcv.loadingChart')}>
            {t('ohlcv.loadingBars', { symbol, timeframe })}
          </div>
        )}
        {!isLoading && error && (
          <div className={styles.error} role="alert">
            <p>
              {t('ohlcv.loadFailedPrefix')} <strong>{symbol}</strong> {timeframe}: {error.message}
            </p>
            <button type="button" onClick={refetch}>
              {t('ohlcv.retry')}
            </button>
          </div>
        )}
        {!isLoading && !error && bars && bars.length === 0 && (
          <div className={styles.empty} role="status" data-testid="ohlcv-empty">
            {t('ohlcv.emptyBars', { symbol, timeframe })}
          </div>
        )}
        {!isLoading && !error && historyClampedDays !== null && (
          <div className={styles.historyNotice} role="status" data-testid="ohlcv-history-clamped">
            <span aria-hidden="true">ℹ</span>{' '}
            {t('ohlcv.historyClampedNotice', { days: historyClampedDays, timeframe })}
          </div>
        )}
        {!isLoading && !error && bars && bars.length > 0 && (
          <CandlestickChart
            bars={bars}
            annotations={mergedAnnotations}
            overlays={overlays}
            trendlines={trendlines}
            divergences={divergences}
            agentModeEnabled={agentModeEnabled}
            symbol={symbol}
            timeframe={timeframe}
            quote={quote}
            onReachLeftEdge={loadOlder}
            historyTriggerEnabled={!isLoadingOlder && !reachedStart}
            ariaLabel={t('ohlcv.chartLabel', { symbol, timeframe, count: bars.length })}
          />
        )}

        {/* Lazy-history affordances (Plan 0030), pinned to the chart's left
            edge. Neither shows once the start of available history is reached. */}
        {isLoadingOlder && !reachedStart && (
          <span
            className={styles.historyLoading}
            role="status"
            data-testid="ohlcv-history-loading"
            aria-label={t('ohlcv.loadingOlder')}
          >
            <span className={styles.spinnerDot} aria-hidden="true" />
            {t('ohlcv.loadingHistory')}
          </span>
        )}
        {olderError && !reachedStart && (
          <div className={styles.historyError} role="alert" data-testid="ohlcv-history-error">
            <span>
              {t('ohlcv.olderBarsError')} {olderError.message}
            </span>
            <button type="button" onClick={loadOlder}>
              {t('ohlcv.retry')}
            </button>
          </div>
        )}
      </div>

      {showToast && backfillError && (
        <Toast
          tone="error"
          message={`Backfill failed (${backfillError.reason}): ${backfillError.message}`}
          onDismiss={() => setToastDismissed(true)}
        />
      )}
    </section>
  )
}

/** Fixed `en-US` formatting so the rendered price is deterministic regardless of
 * the host locale (and asserts cleanly in tests). Two decimals + thousands
 * separators; the currency is appended when the quote carries one. */
function formatPrice(price: number, currency: string): string {
  const num = price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return currency ? `${num} ${currency}` : num
}

export interface PriceHeaderProps {
  symbol: string
  quote: QuoteResponse | null
  /** True while the latest `/quote` poll is failing. The hook keeps the
   * last-known quote on screen, so the displayed price may be stale — dim it and
   * show a `disconnected` badge so the user isn't misled by a frozen number. */
  disconnected?: boolean
}

/**
 * The live current-price header (Plan 0047 phase 6). Shows one symbol-level
 * price fed by `useQuotePoll`, independent of the selected timeframe. Until the
 * first quote resolves (or if every poll has failed), it shows an em dash — it
 * never derives a price from the chart's last bar. The day change renders in the
 * bullish/bearish theme tokens. When `disconnected`, the price is dimmed and a
 * `disconnected` badge appears — the shown value is the last-known one and may be
 * stale (e.g. upstream throttling), so this avoids a silently-frozen price.
 */
export function PriceHeader({
  symbol,
  quote,
  disconnected = false,
}: PriceHeaderProps): JSX.Element {
  const change = quote?.change_pct ?? null
  return (
    <div className={styles.priceHeader} aria-label={t('ohlcv.currentPriceLabel', { symbol })}>
      <span
        className={styles.priceValue}
        data-testid="price-value"
        data-stale={disconnected && quote ? 'true' : undefined}
      >
        {quote ? formatPrice(quote.price, quote.currency) : '—'}
      </span>
      {change !== null && (
        <span
          className={styles.priceChange}
          data-direction={change >= 0 ? 'up' : 'down'}
          data-testid="price-change"
        >
          {change >= 0 ? '+' : ''}
          {change.toFixed(2)}%
        </span>
      )}
      {disconnected && (
        <span
          className={styles.priceStale}
          role="status"
          data-testid="price-disconnected"
          aria-label={t('ohlcv.disconnectedLabel', { symbol })}
        >
          <span className={styles.priceStaleDot} aria-hidden="true" />
          {t('ohlcv.disconnected')}
        </span>
      )}
    </div>
  )
}

/**
 * Merge polled DB annotations (authoritative, ~1 Hz) with the in-memory
 * live-highlights buffer (immediate, from SSE) into one `ChartMarker[]` the chart
 * draws. Dedup key is `(event_ts, pattern, kind)` (Plan 0049 / ADR-0045) — when a
 * polled row arrives for a marker the SSE event already surfaced, the polled row
 * wins. Keying on `pattern` lets two DISTINCT patterns on one bar+direction both
 * survive; persisted annotations carry no `pattern`, so they fall back to the old
 * `(event_ts, kind)` behaviour via the empty segment.
 *
 * Unlike the old path, this no longer down-casts live markers to `Annotation`
 * (which can't hold `neutral_marker`, `pattern`, a span, or `strength`): both
 * sources map into the richer `ChartMarker` so identity/span/strength survive to
 * the chart layer (phase 7 draws spans; the neutral kind renders in phase 6).
 *
 * Exported for direct unit testing.
 */
function markerKey(event_ts: string, pattern: string | null | undefined, kind: string): string {
  return `${event_ts}|${pattern ?? ''}|${kind}`
}

export function mergePolledAndLive(
  polled: Annotation[],
  live: Marker[],
  _symbol: string,
  _timeframe: string,
): ChartMarker[] {
  const polledMarkers: ChartMarker[] = polled.map((a) => ({
    event_ts: a.event_ts,
    kind: a.kind,
    label: a.label ?? null,
  }))
  if (live.length === 0) return polledMarkers
  const seen = new Set(polled.map((a) => markerKey(a.event_ts, null, a.kind)))
  const onlyLive: ChartMarker[] = live
    .filter((m) => !seen.has(markerKey(m.event_ts, m.pattern, m.kind)))
    .map((m) => ({
      event_ts: m.event_ts,
      kind: m.kind,
      label: m.label ?? null,
      pattern: m.pattern ?? null,
      span_start_ts: m.span_start_ts ?? null,
      span_end_ts: m.span_end_ts ?? null,
      strength: m.strength ?? null,
    }))
  return [...polledMarkers, ...onlyLive]
}
