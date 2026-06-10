/**
 * Plan 0052 phase 4 (ADR-0049): the trendline-overlay reconcile, lifted out of
 * `CandlestickChart` into a hook from the start — the chart is flagged
 * god-component debt (the 0047/0049 decomposition follow-up), so this lands as
 * a hook, not inline effects.
 *
 * Owns the `TrendlinePrimitive` lifecycle: attaches it to the candle series
 * lazily on first run, then feeds it specs, theme-resolved colours, and the
 * legend-row visibility on every change. The primitive is disposed by
 * `chart.remove()` on unmount (the chart owns its primitives, same as the span
 * band) — no detach here, which would race the component's cleanup order.
 *
 * MUST be called after the component's chart-creation effect so that, on
 * mount, `seriesRef` is populated before this effect runs (the same ordering
 * contract as `useChartGestures` / `useLazyHistoryTrigger`).
 */
import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'
import type { ISeriesApi } from 'lightweight-charts'

import type { EffectiveTheme } from '../lib/theme'
import { TRENDLINE_LAYER_ID, TrendlinePrimitive, readTrendlineColors } from '../lib/trendlines'
import type { TrendlineSpec } from '../types/events'

export interface UseTrendlinesParams {
  /** The trendline specs to draw (from `chart.show`/`chart.update`). */
  trendlines: ReadonlyArray<TrendlineSpec>
  /** The chart's hidden-layer id set; this hook reads `TRENDLINE_LAYER_ID`. */
  hidden: ReadonlySet<string>
  /** Re-resolves the colour tokens off the DOM when the theme flips. */
  effectiveTheme: EffectiveTheme
}

export function useTrendlines(
  containerRef: RefObject<HTMLDivElement>,
  seriesRef: RefObject<ISeriesApi<'Candlestick'>>,
  { trendlines, hidden, effectiveTheme }: UseTrendlinesParams,
): void {
  const primitiveRef = useRef<TrendlinePrimitive | null>(null)

  useEffect(() => {
    const series = seriesRef.current
    const container = containerRef.current
    if (!series || !container) return
    let primitive = primitiveRef.current
    if (primitive === null) {
      primitive = new TrendlinePrimitive(readTrendlineColors(container))
      series.attachPrimitive(primitive)
      primitiveRef.current = primitive
    }
    // Recolours in place on a theme flip (`effectiveTheme` in the deps re-runs
    // the token read); the primitive persists — no remount, no re-attach.
    primitive.setColors(readTrendlineColors(container))
    primitive.setTrendlines(trendlines)
    primitive.setVisible(!hidden.has(TRENDLINE_LAYER_ID))
  }, [containerRef, seriesRef, trendlines, hidden, effectiveTheme])
}
