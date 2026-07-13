/**
 * Agent-overlay line-series reconcile (Plan 0007 phase 4.5 / Plan 0049 phase 9 —
 * lifted verbatim out of `CandlestickChart`'s bars effect in the Plan 0072 phase 8
 * decomposition, no behaviour change).
 *
 * Reconciles the single-line agent overlays (ema/sma; unsupported kinds warn and
 * are ignored; price_line + supertrend are handled elsewhere): add new series,
 * remove gone/toggled-off ones, recompute data for the kept ones (bars may have
 * moved). New series take their initial colour + width from the resolved style
 * (theme read off the ref so a flip doesn't re-create series — the restyle effect
 * re-applies existing ones in place). Calls `syncTestRenderHook` so the render
 * test-hook reflects the reconciled overlay set.
 *
 * MUST be called after the chart-creation + bars effects so the refs + main series
 * exist. `rebuildToken` (candleType) re-runs it after a chart rebuild.
 */
import { useEffect } from 'react'
import type { RefObject } from 'react'
import { LineSeries } from 'lightweight-charts'
import type { IChartApi } from 'lightweight-charts'

import {
  DEFAULT_OVERLAY_LINE_WIDTH,
  overlayKey,
  overlayStyleColor,
  overlayStyleWidth,
  type OverlayEntry,
} from '../lib/chartSeries'
import { resolveChartStyle } from '../lib/chartStyle'
import {
  computeOverlayData,
  isOscillatorOverlay,
  isStructureOverlay,
  isSupportedOverlay,
  overlayColorFor,
  overlayLayerId,
} from '../lib/overlays'
import type { EffectiveTheme } from '../lib/theme'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'

export interface UseOverlaySeriesParams {
  bars: Bar[]
  overlays: ReadonlyArray<OverlaySpec> | undefined
  hidden: ReadonlySet<string>
  /** Read (not a dep) so a theme flip doesn't re-create series — the restyle
   * effect re-applies existing ones in place. */
  effectiveThemeRef: RefObject<EffectiveTheme>
  rebuildToken: unknown
  syncTestRenderHook: () => void
}

export function useOverlaySeries(
  chartRef: RefObject<IChartApi | null>,
  containerRef: RefObject<HTMLDivElement>,
  overlaySeriesRef: RefObject<Map<string, OverlayEntry>>,
  {
    bars,
    overlays,
    hidden,
    effectiveThemeRef,
    rebuildToken,
    syncTestRenderHook,
  }: UseOverlaySeriesParams,
): void {
  useEffect(() => {
    const chart = chartRef.current
    const overlaySeries = overlaySeriesRef.current
    if (!chart || !overlaySeries) return

    // Resolve the style once for any series this reconcile CREATES (initial colour
    // + width, overrides layered). Existing series get re-applied in place by the
    // restyle effect, so a theme flip doesn't need to re-run this.
    const overlayStyle =
      containerRef.current && effectiveThemeRef.current
        ? resolveChartStyle(containerRef.current, effectiveThemeRef.current)
        : null

    const desired = new Map<string, OverlaySpec>()
    for (const spec of overlays ?? []) {
      // price_line overlays are horizontal lines, reconciled elsewhere — not line
      // series, and not an "unsupported" warning case.
      if (spec.kind === 'price_line') continue
      // supertrend is a two-masked-series overlay, reconciled separately — skip
      // the generic single-series path (and its "unsupported" warning).
      if (spec.kind === 'supertrend') continue
      // ichimoku draws its five lines + cloud as a dedicated primitive
      // (`useIchimokuSeries`), not a generic line series — skip it here too.
      if (spec.kind === 'ichimoku') continue
      // Oscillators draw in their own sub-panes (`useOscillatorPanes`), not the
      // price pane — skip the generic single-line path (Plan 0091 phase 6).
      if (isOscillatorOverlay(spec.kind)) continue
      // Price-structure overlays draw via dedicated hooks (`useStructureLevels`,
      // `useAnchoredVwapSeries`) — skip the generic single-line path (Plan 0092).
      if (isStructureOverlay(spec.kind)) continue
      if (!isSupportedOverlay(spec.kind)) {
        console.warn(
          `[CandlestickChart] unsupported overlay kind "${spec.kind}" — ignored (MVP renders ema/sma only)`,
        )
        continue
      }
      // A layer toggled off in the legend is removed below (absent from `desired`)
      // and re-added when re-checked.
      if (hidden.has(overlayLayerId(spec))) continue
      desired.set(overlayKey(spec), spec)
    }

    // Remove series no longer requested.
    for (const [key, entry] of overlaySeries) {
      if (!desired.has(key)) {
        chart.removeSeries(entry.series)
        overlaySeries.delete(key)
      }
    }

    // Add new series + recompute data for all kept ones (bars may have moved).
    for (const [key, spec] of desired) {
      let entry = overlaySeries.get(key)
      if (entry === undefined) {
        const color = overlayStyle ? overlayStyleColor(spec, overlayStyle) : overlayColorFor(spec)
        const series = chart.addSeries(LineSeries, {
          color,
          lineWidth: overlayStyle
            ? overlayStyleWidth(spec, overlayStyle)
            : DEFAULT_OVERLAY_LINE_WIDTH,
          priceLineVisible: false,
          lastValueVisible: false,
        })
        entry = { spec, series }
        overlaySeries.set(key, entry)
      }
      entry.series.setData(computeOverlayData(bars, spec))
    }

    syncTestRenderHook()
  }, [
    chartRef,
    containerRef,
    overlaySeriesRef,
    bars,
    overlays,
    hidden,
    effectiveThemeRef,
    rebuildToken,
    syncTestRenderHook,
  ])
}
