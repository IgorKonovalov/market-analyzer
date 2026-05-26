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
import { useEffect, useMemo, useState } from 'react'

import { CandlestickChart } from '../components/CandlestickChart'
import { SymbolPicker } from '../components/SymbolPicker'
import type { Timeframe } from '../components/SymbolPicker'
import { Toast } from '../components/Toast'
import { useAnnotationsPoll } from '../hooks/useAnnotationsPoll'
import { useBackfillState } from '../hooks/useBackfillState'
import { useOhlcv } from '../hooks/useOhlcv'
import type { Marker, OverlaySpec } from '../types/events'
import type { Annotation } from '../types/sidecar/annotation'
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
  onSymbolChange,
  onTimeframeChange,
  onRefresh,
}: OhlcvViewProps): JSX.Element {
  const start = useMemo(() => new Date(range_start), [range_start])
  const end = useMemo(() => new Date(range_end), [range_end])

  const { bars, isLoading, error, refetch } = useOhlcv({ symbol, timeframe, start, end })
  const { annotations } = useAnnotationsPoll({ symbol, timeframe, start, end })
  const { isBackfilling, error: backfillError } = useBackfillState({ symbol, timeframe, refetch })

  // A fresh backfill failure re-shows the toast even if a prior one was dismissed.
  const [toastDismissed, setToastDismissed] = useState(false)
  useEffect(() => {
    if (backfillError) setToastDismissed(false)
  }, [backfillError])
  const showToast = backfillError !== null && !toastDismissed

  const mergedAnnotations = useMemo(
    () => mergePolledAndLive(annotations, liveHighlights, symbol, timeframe),
    [annotations, liveHighlights, symbol, timeframe],
  )

  return (
    <section className={styles.root} aria-label={`OHLCV view for ${symbol} ${timeframe}`}>
      <header className={styles.toolbar}>
        <SymbolPicker
          symbol={symbol}
          timeframe={timeframe}
          onSymbolChange={onSymbolChange}
          onTimeframeChange={onTimeframeChange}
          disabled={isLoading}
        />
        <button type="button" className={styles.refresh} onClick={onRefresh} disabled={isLoading}>
          Refresh
        </button>
        {isBackfilling && (
          <span
            className={styles.backfillSpinner}
            role="status"
            data-testid="ohlcv-backfill-spinner"
            aria-label={`Backfilling ${symbol} ${timeframe}`}
          >
            <span className={styles.spinnerDot} aria-hidden="true" />
            Backfilling…
          </span>
        )}
      </header>

      <div className={styles.body}>
        {isLoading && (
          <div className={styles.skeleton} role="status" aria-label="Loading chart">
            Loading {symbol} {timeframe}…
          </div>
        )}
        {!isLoading && error && (
          <div className={styles.error} role="alert">
            <p>
              Failed to load <strong>{symbol}</strong> {timeframe}: {error.message}
            </p>
            <button type="button" onClick={refetch}>
              Retry
            </button>
          </div>
        )}
        {!isLoading && !error && bars && bars.length === 0 && (
          <div className={styles.empty} role="status" data-testid="ohlcv-empty">
            No bars for {symbol} {timeframe} in this window.
          </div>
        )}
        {!isLoading && !error && bars && bars.length > 0 && (
          <CandlestickChart
            bars={bars}
            annotations={mergedAnnotations}
            overlays={overlays}
            ariaLabel={`Candlestick chart for ${symbol} ${timeframe}, ${bars.length} bars`}
          />
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

/**
 * Merge polled DB annotations (authoritative, ~1 Hz) with the in-memory
 * live-highlights buffer (immediate, from SSE). Dedup key is
 * `(event_ts, kind)` — when the polled row arrives for a marker that the
 * SSE event already surfaced, the polled row wins (it carries the full
 * Annotation shape, including `id`/`agent_id`/`created_at`).
 *
 * Live markers without a polled counterpart are upcast to Annotation
 * shape with `agent_id: 'live'` so the chart marker layer can treat the
 * unified list as Annotation[]. The `live` id signals provenance for any
 * future filtering.
 *
 * Exported for direct unit testing.
 */
export function mergePolledAndLive(
  polled: Annotation[],
  live: Marker[],
  symbol: string,
  timeframe: string,
): Annotation[] {
  if (live.length === 0) return polled
  const seen = new Set(polled.map((a) => `${a.event_ts}|${a.kind}`))
  const onlyLive: Annotation[] = live
    .filter((m) => !seen.has(`${m.event_ts}|${m.kind}`))
    .map((m) => ({
      symbol,
      timeframe,
      event_ts: m.event_ts,
      kind: m.kind,
      label: m.label ?? null,
      agent_id: 'live',
    }))
  return [...polled, ...onlyLive]
}
