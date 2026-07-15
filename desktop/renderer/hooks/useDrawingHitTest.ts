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
 * Pure: resolve a fractional bar-logical index + a price to a `(time, price)`
 * anchor.
 *
 * - Over the loaded bars, the logical rounds to the nearest bar and takes its
 *   `event_ts` (a drawing starts from a known bar); the price snaps to that bar's
 *   nearest OHLC when `snapPrice` (the default, the 0097 line-tool magnet) or is the
 *   raw cursor price otherwise (a Plan 0104 position/range places at any price).
 * - PAST the last bar, the time is EXTRAPOLATED from the last bar spacing so a
 *   drawing can extend into the future (the renderer maps off-grid times, ADR-0059);
 *   there is no bar to snap to, so the price is the raw cursor price regardless of
 *   `snapPrice`. This is the "draw anywhere into the future" smoke follow-up.
 *
 * Returns `null` only when there are no bars to resolve against.
 */
export function snapAnchor(
  bars: ReadonlyArray<Bar>,
  logical: number,
  price: number,
  snapPrice = true,
): TimePricePoint | null {
  if (bars.length === 0) return null
  const last = bars.length - 1
  // Future anchor: extrapolate a timestamp so the drawing can reach past the last
  // candle. Needs two bars to know the spacing; a single-bar chart falls through to
  // the clamp below.
  if (logical > last && bars.length >= 2) {
    const lastMs = new Date(bars[last].event_ts).getTime()
    const stepMs = lastMs - new Date(bars[last - 1].event_ts).getTime()
    const ts = new Date(lastMs + (logical - last) * stepMs).toISOString()
    return { ts, price }
  }
  const idx = Math.min(last, Math.max(0, Math.round(logical)))
  const bar = bars[idx]
  return { ts: bar.event_ts, price: snapPrice ? nearestOhlc(bar, price) : price }
}

export interface UseDrawingHitTestResult {
  /** Convert a container-relative pixel to a `(time, price)` anchor whose time is
   * on the nearest bar — with the price snapped to that bar's OHLC (`snapPrice`,
   * the default) or left at the raw cursor price (`snapPrice=false`, the position/
   * range tools). `null` when the chart/series/bars can't resolve it. */
  snapPixel: (x: number, y: number, snapPrice?: boolean) => TimePricePoint | null
}

export function useDrawingHitTest(
  chartRef: RefObject<IChartApi | null>,
  seriesRef: RefObject<ISeriesApi<'Candlestick' | 'Bar' | 'Line' | 'Area'> | null>,
  bars: ReadonlyArray<Bar>,
): UseDrawingHitTestResult {
  const snapPixel = useCallback(
    (x: number, y: number, snapPrice = true): TimePricePoint | null => {
      const chart = chartRef.current
      const series = seriesRef.current
      if (!chart || !series) return null
      const logical = chart.timeScale().coordinateToLogical(x)
      const price = series.coordinateToPrice(y)
      if (logical === null || price === null) return null
      return snapAnchor(bars, logical, price, snapPrice)
    },
    [chartRef, seriesRef, bars],
  )
  return { snapPixel }
}
