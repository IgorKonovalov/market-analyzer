/**
 * Layers-legend state (Plan 0047 phase 9 — lifted verbatim out of
 * `CandlestickChart` in the Plan 0072 phase 8 decomposition, no behaviour change).
 *
 * Resolves the chart-style + trendline colour tokens off the container DOM (so it
 * must run after mount), builds the ordered `ChartLayer[]` via the pure
 * `buildChartLayers`, and owns + returns the legend state. Recomputes when the
 * inputs or theme change.
 */
import { useEffect, useState } from 'react'
import type { RefObject } from 'react'

import { chartColorsFrom } from '../lib/chartSeries'
import { resolveChartStyle } from '../lib/chartStyle'
import type { CandlestickPatternGroup } from '../lib/candleGroups'
import { buildChartLayers } from '../lib/layersLegend'
import { readTrendlineColors } from '../lib/trendlines'
import type { EffectiveTheme } from '../lib/theme'
import type { ChartLayer } from '../components/LayersPanel'
import type { OverlaySpec, TrendlineSpec } from '../types/events'

export interface UseLayersLegendParams {
  overlays: ReadonlyArray<OverlaySpec> | undefined
  candleGroups: CandlestickPatternGroup[]
  enabledCandleGroups: ReadonlySet<string>
  visibleTrendlines: ReadonlyArray<TrendlineSpec>
  hidden: ReadonlySet<string>
  /** Whether the always-on OBV strip is drawn (Plan 0076 phase 2) — the chart
   * passes `bars.length > 0`. Adds a single toggleable OBV legend row. */
  hasObv: boolean
  effectiveTheme: EffectiveTheme
  styleVersion: number
}

export function useLayersLegend(
  containerRef: RefObject<HTMLDivElement>,
  {
    overlays,
    candleGroups,
    enabledCandleGroups,
    visibleTrendlines,
    hidden,
    hasObv,
    effectiveTheme,
    styleVersion,
  }: UseLayersLegendParams,
): ChartLayer[] {
  const [layers, setLayers] = useState<ChartLayer[]>([])
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const style = resolveChartStyle(container, effectiveTheme)
    const colors = chartColorsFrom(style)
    const trendlineColors = readTrendlineColors(container)
    setLayers(
      buildChartLayers({
        overlays,
        candleGroups,
        enabledCandleGroups,
        visibleTrendlines,
        hidden,
        hasObv,
        style,
        colors,
        trendlineColors,
      }),
    )
  }, [
    containerRef,
    overlays,
    candleGroups,
    enabledCandleGroups,
    visibleTrendlines,
    hidden,
    hasObv,
    effectiveTheme,
    styleVersion,
  ])
  return layers
}
