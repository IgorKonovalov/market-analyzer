/**
 * Snap + coordinate helpers for the drawing edit engine (Plan 0097 phase 2,
 * ADR-0091).
 *
 * The point-to-shape distance and endpoint hit-testing live on the
 * `DrawingPrimitive` (it holds the painted pixel geometry — `lib/drawings.ts`,
 * reusing `pointSegmentDistance`). What lives here is the other half: turning a
 * pointer pixel into a snapped `(time, price)` anchor, so a placed or dragged
 * endpoint re-anchors to a real bar's OHLC rather than floating between candles.
 *
 * The pure core (`nearestOhlc` / `snapAnchor`) is chart-free and unit-tested; the
 * `useDrawingHitTest` hook binds it to the live chart's coordinate scales so the
 * tool machine can call `snapPixel(x, y)` on a click or a drag release.
 */
import { useCallback } from 'react'
import type { RefObject } from 'react'
import type { IChartApi, ISeriesApi } from 'lightweight-charts'

import type { TimePricePoint } from '../types/events'
import type { Bar } from '../types/sidecar/bar'

/** The bar OHLC value nearest `price` — the snap target on the resolved bar. Ties
 * resolve to the first of open/high/low/close (deterministic). */
export function nearestOhlc(bar: Bar, price: number): number {
  const candidates = [bar.open, bar.high, bar.low, bar.close]
  let best = candidates[0]
  let bestDist = Math.abs(candidates[0] - price)
  for (const v of candidates.slice(1)) {
    const d = Math.abs(v - price)
    if (d < bestDist) {
      bestDist = d
      best = v
    }
  }
  return best
}

/**
 * Pure: resolve a fractional bar-logical index + a price to a snapped
 * `(time, price)` anchor — round + clamp the logical to a real bar, take that
 * bar's `event_ts`, and snap the price to the bar's nearest OHLC. Returns `null`
 * when there are no bars to snap to.
 */
export function snapAnchor(
  bars: ReadonlyArray<Bar>,
  logical: number,
  price: number,
): TimePricePoint | null {
  if (bars.length === 0) return null
  const idx = Math.min(bars.length - 1, Math.max(0, Math.round(logical)))
  const bar = bars[idx]
  return { ts: bar.event_ts, price: nearestOhlc(bar, price) }
}

export interface UseDrawingHitTestResult {
  /** Convert a container-relative pixel to a snapped `(time, price)` anchor on the
   * nearest bar's OHLC, or `null` when the chart/series/bars can't resolve it. */
  snapPixel: (x: number, y: number) => TimePricePoint | null
}

export function useDrawingHitTest(
  chartRef: RefObject<IChartApi | null>,
  seriesRef: RefObject<ISeriesApi<'Candlestick' | 'Bar' | 'Line' | 'Area'> | null>,
  bars: ReadonlyArray<Bar>,
): UseDrawingHitTestResult {
  const snapPixel = useCallback(
    (x: number, y: number): TimePricePoint | null => {
      const chart = chartRef.current
      const series = seriesRef.current
      if (!chart || !series) return null
      const logical = chart.timeScale().coordinateToLogical(x)
      const price = series.coordinateToPrice(y)
      if (logical === null || price === null) return null
      return snapAnchor(bars, logical, price)
    },
    [chartRef, seriesRef, bars],
  )
  return { snapPixel }
}
