/**
 * Live forming-bar update (Plan 0049 phase 10 — lifted verbatim out of
 * `CandlestickChart` in the Plan 0072 phase 8 decomposition, no behaviour change).
 *
 * Feeds the already-polled `/quote` into the chart's CURRENT (forming) bar via
 * `series.update()` — close tracks the quote, high/low extend — but ONLY when the
 * quote's `as_of` falls within the latest bar's period. A quote that predates the
 * latest bar, or has crossed into a not-yet-fetched new period, touches nothing:
 * we never rewrite a closed bar nor fabricate a new one (that is a refetch/SSE
 * concern). No new fetch, no setData — `series.update()` at the last bar's time
 * updates it in place. No lookahead: this is the live current bar, not historical
 * replay.
 *
 * MUST be called after the component's chart-creation effect so `seriesRef` is
 * populated on mount.
 */
import { useEffect } from 'react'
import type { RefObject } from 'react'
import type { ISeriesApi, UTCTimestamp } from 'lightweight-charts'

import type { MainSeries } from '../lib/chartSeries'
import type { CandleSeriesType } from '../lib/chartStyle'
import { timeframeDurationMs } from '../lib/timeframes'
import type { Bar } from '../types/sidecar/bar'
import type { QuoteResponse } from '../types/sidecar/quote-response'

export interface UseFormingBarParams {
  quote: QuoteResponse | null | undefined
  bars: Bar[]
  timeframe: string | undefined
  /** The main series' render type — line/area track a single value, otherwise OHLC. */
  candleType: CandleSeriesType
}

export function useFormingBar(
  seriesRef: RefObject<MainSeries | null>,
  { quote, bars, timeframe, candleType }: UseFormingBarParams,
): void {
  useEffect(() => {
    const series = seriesRef.current
    if (!series || !quote || bars.length === 0) return
    const periodMs = timeframeDurationMs(timeframe)
    if (periodMs === null) return
    const last = bars[bars.length - 1]
    const lastStartMs = new Date(last.event_ts).getTime()
    const asOfMs = new Date(quote.as_of).getTime()
    // Outside the forming bar's [start, start + period) window → leave every bar.
    if (asOfMs < lastStartMs || asOfMs >= lastStartMs + periodMs) return
    const time = Math.floor(lastStartMs / 1000) as UTCTimestamp
    if (candleType === 'line' || candleType === 'area') {
      // Line/area track a single value (the forming close).
      ;(series as ISeriesApi<'Line'>).update({ time, value: quote.price })
    } else {
      ;(series as ISeriesApi<'Candlestick'>).update({
        time,
        open: last.open,
        high: Math.max(last.high, quote.price),
        low: Math.min(last.low, quote.price),
        close: quote.price,
      })
    }
  }, [seriesRef, quote, bars, timeframe, candleType])
}
