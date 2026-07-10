/**
 * In-place chart restyle on theme / chart-style change (Plan 0068 phase 2 —
 * lifted verbatim out of `CandlestickChart` in the Plan 0072 phase 8
 * decomposition, no behaviour change).
 *
 * Re-applies the EXISTING chart's colours + line widths when the effective theme
 * changes OR the user mutates the chart-style store. One `resolveChartStyle` pass,
 * pushed via `applyOptions` — NO remount (the creation effect is untouched); also
 * runs once on mount, idempotent with the creation values. Colour AND width both
 * flow here, so a colour or a width override lands in place on any mounted chart.
 *
 * MUST be called after the chart-creation effect so the refs are populated.
 */
import { useEffect } from 'react'
import type { RefObject } from 'react'
import type { IChartApi, ISeriesApi, LineWidth } from 'lightweight-charts'

import {
  applyMainColors,
  chartColorsFrom,
  overlayStyleColor,
  overlayStyleWidth,
  type MainSeries,
  type OverlayEntry,
} from '../lib/chartSeries'
import { resolveChartStyle } from '../lib/chartStyle'
import type { CandleSeriesType } from '../lib/chartStyle'
import type { EffectiveTheme } from '../lib/theme'

export interface ChartRestyleRefs {
  containerRef: RefObject<HTMLDivElement>
  chartRef: RefObject<IChartApi | null>
  seriesRef: RefObject<MainSeries | null>
  volumeSeriesRef: RefObject<ISeriesApi<'Histogram'> | null>
  volumeMaSeriesRef: RefObject<ISeriesApi<'Line'> | null>
  vwapSeriesRef: RefObject<ISeriesApi<'Line'> | null>
  obvSeriesRef: RefObject<ISeriesApi<'Line'> | null>
  overlaySeriesRef: RefObject<Map<string, OverlayEntry>>
  supertrendSeriesRef: RefObject<Map<string, { up: ISeriesApi<'Line'>; down: ISeriesApi<'Line'> }>>
}

export interface UseChartRestyleParams {
  effectiveTheme: EffectiveTheme
  styleVersion: number
  candleType: CandleSeriesType
}

export function useChartRestyle(
  refs: ChartRestyleRefs,
  { effectiveTheme, styleVersion, candleType }: UseChartRestyleParams,
): void {
  const {
    containerRef,
    chartRef,
    seriesRef,
    volumeSeriesRef,
    volumeMaSeriesRef,
    vwapSeriesRef,
    obvSeriesRef,
    overlaySeriesRef,
    supertrendSeriesRef,
  } = refs
  useEffect(() => {
    const container = containerRef.current
    const chart = chartRef.current
    const candlestick = seriesRef.current
    if (!container || !chart || !candlestick) return
    const style = resolveChartStyle(container, effectiveTheme)
    const colors = chartColorsFrom(style)
    chart.applyOptions({
      layout: { textColor: colors.text },
      grid: {
        vertLines: { color: colors.border },
        horzLines: { color: colors.border },
      },
    })
    applyMainColors(candlestick, candleType, colors)
    volumeSeriesRef.current?.applyOptions({ color: colors.volume })
    volumeMaSeriesRef.current?.applyOptions({
      color: colors.volumeMa,
      lineWidth: style.widths.volumeMa as LineWidth,
    })
    vwapSeriesRef.current?.applyOptions({
      color: colors.vwap,
      lineWidth: style.widths.vwap as LineWidth,
    })
    obvSeriesRef.current?.applyOptions({
      color: colors.obv,
      lineWidth: style.widths.obv as LineWidth,
    })
    for (const { spec, series } of overlaySeriesRef.current?.values() ?? []) {
      series.applyOptions({
        color: overlayStyleColor(spec, style),
        lineWidth: overlayStyleWidth(spec, style),
      })
    }
    // Supertrend's two masked series recolor from the bull/bear tokens in place.
    for (const { up, down } of supertrendSeriesRef.current?.values() ?? []) {
      up.applyOptions({ color: colors.markerBullish })
      down.applyOptions({ color: colors.markerBearish })
    }
  }, [
    containerRef,
    chartRef,
    seriesRef,
    volumeSeriesRef,
    volumeMaSeriesRef,
    vwapSeriesRef,
    obvSeriesRef,
    overlaySeriesRef,
    supertrendSeriesRef,
    effectiveTheme,
    styleVersion,
    candleType,
  ])
}
