/**
 * Price-line reconcile (Plan 0047 phase 9 — lifted verbatim out of
 * `CandlestickChart` in the Plan 0072 phase 8 decomposition, no behaviour change).
 *
 * Reconciles horizontal `price_line` overlays (S/R levels the agent pushes) on
 * the main series: a line toggled off in the legend is removed; re-checking
 * re-creates it. Colours resolve from the theme tokens, so a theme flip recolours
 * the kept lines in place. `styleVersion`/`candleType` re-run it after a chart
 * restyle / rebuild.
 *
 * MUST be called after the component's chart-creation effect so `seriesRef` is
 * populated on mount.
 */
import { useEffect } from 'react'
import type { RefObject } from 'react'
import type { IPriceLine } from 'lightweight-charts'

import { chartColorsFrom, type MainSeries } from '../lib/chartSeries'
import { resolveChartStyle } from '../lib/chartStyle'
import { priceLineColor, priceLineId } from '../lib/priceLines'
import type { EffectiveTheme } from '../lib/theme'
import type { OverlaySpec } from '../types/events'

export interface UsePriceLinesParams {
  overlays: ReadonlyArray<OverlaySpec> | undefined
  hidden: ReadonlySet<string>
  effectiveTheme: EffectiveTheme
  styleVersion: number
  rebuildToken: unknown
}

export function usePriceLines(
  seriesRef: RefObject<MainSeries | null>,
  containerRef: RefObject<HTMLDivElement>,
  priceLinesRef: RefObject<Map<string, IPriceLine>>,
  { overlays, hidden, effectiveTheme, styleVersion, rebuildToken }: UsePriceLinesParams,
): void {
  useEffect(() => {
    const series = seriesRef.current
    const container = containerRef.current
    const priceLines = priceLinesRef.current
    if (!series || !container || !priceLines) return
    const colors = chartColorsFrom(resolveChartStyle(container, effectiveTheme))
    const desired = new Map<string, OverlaySpec>()
    for (const spec of overlays ?? []) {
      if (spec.kind !== 'price_line') continue
      if (hidden.has(priceLineId(spec))) continue
      desired.set(priceLineId(spec), spec)
    }
    for (const [id, line] of priceLines) {
      if (!desired.has(id)) {
        series.removePriceLine(line)
        priceLines.delete(id)
      }
    }
    for (const [id, spec] of desired) {
      const color = priceLineColor(spec, colors)
      const existing = priceLines.get(id)
      if (existing === undefined) {
        const line = series.createPriceLine({
          price: spec.price ?? 0,
          color,
          axisLabelVisible: true,
          title: spec.label ?? '',
        })
        priceLines.set(id, line)
      } else {
        existing.applyOptions({ color })
      }
    }
    // `rebuildToken` (candleType) re-creates the price lines on the fresh series
    // after a rebuild.
  }, [
    seriesRef,
    containerRef,
    priceLinesRef,
    overlays,
    hidden,
    effectiveTheme,
    styleVersion,
    rebuildToken,
  ])
}
