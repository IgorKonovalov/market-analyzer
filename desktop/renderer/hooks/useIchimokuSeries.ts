/**
 * Ichimoku overlay reconcile (Plan 0073 phase 4, ADR-0067). FEEDS an
 * already-attached `IchimokuPrimitive` the computed geometries, theme-resolved
 * colours, and legend-row visibility on every change — it does NOT attach the
 * primitive (that happens in the component's chart-creation effect, mirroring the
 * span/trendline primitives, so its lifecycle is the chart's and it survives
 * StrictMode / candle-type rebuilds).
 *
 * Also reserves trailing space on the time scale (`rightOffset = displacement`)
 * whenever an Ichimoku overlay is visible, so the cloud projected `displacement`
 * bars past the last candle lands in on-screen axis space rather than off the
 * right edge; it resets to 0 when no Ichimoku overlay is shown.
 *
 * MUST be called after the chart-creation + bars effects so the refs + main
 * series exist. `rebuildToken` (candleType) re-runs it after a chart rebuild.
 */
import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'
import type { IChartApi } from 'lightweight-charts'

import { overlayLayerId } from '../lib/overlays'
import {
  IchimokuPrimitive,
  computeIchimokuGeometry,
  ichimokuPeriods,
  readIchimokuColors,
  type IchimokuGeometry,
} from '../lib/ichimoku'
import type { EffectiveTheme } from '../lib/theme'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'

export interface UseIchimokuSeriesParams {
  bars: Bar[]
  overlays: ReadonlyArray<OverlaySpec> | undefined
  hidden: ReadonlySet<string>
  /** Re-resolves the colour tokens off the DOM when the theme flips. */
  effectiveTheme: EffectiveTheme
  /** Re-feeds the freshly-attached primitive after a candle-type rebuild. */
  rebuildToken?: unknown
}

export function useIchimokuSeries(
  chartRef: RefObject<IChartApi | null>,
  containerRef: RefObject<HTMLDivElement>,
  ichimokuPrimitiveRef: RefObject<IchimokuPrimitive | null>,
  { bars, overlays, hidden, effectiveTheme, rebuildToken }: UseIchimokuSeriesParams,
): void {
  // Whether we currently hold a non-default `rightOffset` for a visible cloud, so
  // we reset it exactly once when the last Ichimoku overlay goes away — and never
  // touch `rightOffset` at all on charts that never use Ichimoku.
  const reservedRef = useRef(false)
  useEffect(() => {
    const primitive = ichimokuPrimitiveRef.current
    const chart = chartRef.current
    const container = containerRef.current
    if (!primitive || !chart || !container) return

    primitive.setColors(readIchimokuColors(container))

    const geometries: IchimokuGeometry[] = []
    let maxDisplacement = 0
    let hasSpec = false
    for (const spec of overlays ?? []) {
      if (spec.kind !== 'ichimoku') continue
      hasSpec = true
      // A layer toggled off in the legend removes the whole overlay.
      if (hidden.has(overlayLayerId(spec))) continue
      geometries.push(computeIchimokuGeometry(bars, spec))
      maxDisplacement = Math.max(maxDisplacement, ichimokuPeriods(spec).displacement)
    }
    primitive.setGeometries(geometries)

    // Reserve future axis space so the projected cloud is visible past the last
    // candle; reset to the default (0) when the overlay is toggled off or removed.
    // Only ever touch `rightOffset` on a chart that uses Ichimoku (a chart that
    // never does keeps the library default untouched).
    if (hasSpec) {
      const offset = geometries.length > 0 ? maxDisplacement : 0
      chart.timeScale().applyOptions({ rightOffset: offset })
      reservedRef.current = offset > 0
    } else if (reservedRef.current) {
      chart.timeScale().applyOptions({ rightOffset: 0 })
      reservedRef.current = false
    }
  }, [
    chartRef,
    containerRef,
    ichimokuPrimitiveRef,
    bars,
    overlays,
    hidden,
    effectiveTheme,
    rebuildToken,
  ])
}
