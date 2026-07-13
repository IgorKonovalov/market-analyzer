/**
 * Hover tooltip (Plan 0047 phase 8 / Plan 0067 phase 2 / Plan 0071 follow-up —
 * lifted verbatim out of `CandlestickChart` in the Plan 0072 phase 8
 * decomposition, no behaviour change).
 *
 * On crosshair move, shows a hovered pattern marker's name and/or each overlay
 * line's name + value at that bar, and any trendline under the cursor. Reads only
 * data already in renderer state (the DRAWN markers + the overlay readings the
 * chart pulls from `seriesData`) — no sidecar call. Keying off `drawnMarkers`
 * (not raw annotations) means a toggled-off / master-hidden group shows no hover.
 * Hovering a marker also outlines its pattern's bar(s) via the span primitive's
 * highlight. Owns the tooltip state and RETURNS it for the component to render;
 * unsubscribes on unmount / rebuild.
 *
 * MUST be called after the chart-creation effect so the refs are populated.
 */
import { useEffect, useState } from 'react'
import type { RefObject } from 'react'
import type { LineData, IChartApi, MouseEventParams, UTCTimestamp } from 'lightweight-charts'

import type { OverlayEntry } from '../lib/chartSeries'
import type { ChartMarker } from '../lib/markers'
import { PatternSpanPrimitive, markerHighlightSpan } from '../lib/spans'
import { TrendlinePrimitive } from '../lib/trendlines'
import { DivergencePrimitive } from '../lib/divergences'
import {
  type OverlayReading,
  type TooltipContent,
  divergenceTooltipText,
  overlayLabel,
  tooltipAtTime,
  trendlineTooltipText,
} from '../lib/tooltip'
import { useLocale } from './useLocalePref'

export interface TooltipState {
  content: TooltipContent
  x: number
  y: number
}

export interface UseChartTooltipParams {
  drawnMarkers: ChartMarker[]
  rebuildToken: unknown
}

export function useChartTooltip(
  chartRef: RefObject<IChartApi | null>,
  overlaySeriesRef: RefObject<Map<string, OverlayEntry>>,
  spanPrimitiveRef: RefObject<PatternSpanPrimitive | null>,
  trendlinePrimitiveRef: RefObject<TrendlinePrimitive | null>,
  divergencePricePrimitiveRef: RefObject<DivergencePrimitive | null>,
  { drawnMarkers, rebuildToken }: UseChartTooltipParams,
): TooltipState | null {
  const [tooltip, setTooltip] = useState<TooltipState | null>(null)
  // The hovered candlestick's meaning line is localizable glossary content (Plan
  // 0085); read the active locale so it re-subscribes and re-localizes on switch.
  const locale = useLocale()
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const handler = (param: MouseEventParams): void => {
      // A trendline can extend past the last bar, so the pointer may be over a
      // line while `param.time` is undefined — gate only on `point`, and compute
      // the time-keyed marker/overlay content only when a time is present.
      if (param.point === undefined) {
        setTooltip(null)
        spanPrimitiveRef.current?.setHighlight(null)
        return
      }
      const readings: OverlayReading[] = []
      if (param.time !== undefined) {
        for (const { spec, series } of overlaySeriesRef.current?.values() ?? []) {
          const datum = param.seriesData.get(series)
          const value = datum !== undefined ? (datum as LineData).value : undefined
          if (typeof value === 'number') {
            readings.push({ label: overlayLabel(spec), value })
          }
        }
      }
      // The DRAWN markers on the hovered bar (only enabled groups) — drives both
      // the tooltip pattern name and the pattern-outline highlight.
      const hoveredMarkers =
        param.time !== undefined
          ? drawnMarkers.filter(
              (m) => Math.floor(new Date(m.event_ts).getTime() / 1000) === param.time,
            )
          : []
      spanPrimitiveRef.current?.setHighlight(markerHighlightSpan(hoveredMarkers))
      const timeContent =
        param.time !== undefined
          ? tooltipAtTime(param.time as UTCTimestamp, drawnMarkers, readings, locale)
          : null
      // Trendline under the cursor (Plan 0067 phase 2): the primitive hit-tests
      // the hovered pixel against its drawn segments and returns the spec, if any.
      const hovered =
        trendlinePrimitiveRef.current?.hitTestTrendline(param.point.x, param.point.y) ?? null
      const trendlines = hovered ? [trendlineTooltipText(hovered)] : []
      // Divergence under the cursor (Plan 0091 phase 9): the price-pane primitive
      // hit-tests the hovered pixel against its price-pivot segments — same pane and
      // coordinate space as the trendline hit-test.
      const hoveredDivergence =
        divergencePricePrimitiveRef.current?.hitTestDivergence(param.point.x, param.point.y) ?? null
      const divergences = hoveredDivergence
        ? [divergenceTooltipText(hoveredDivergence, locale)]
        : []
      const markers = timeContent?.markers ?? []
      const overlays = timeContent?.overlays ?? []
      const markerMeaning = timeContent?.markerMeaning
      if (
        markers.length === 0 &&
        overlays.length === 0 &&
        trendlines.length === 0 &&
        divergences.length === 0
      ) {
        setTooltip(null)
        return
      }
      setTooltip({
        content: { markers, overlays, trendlines, divergences, markerMeaning },
        x: param.point.x,
        y: param.point.y,
      })
    }
    chart.subscribeCrosshairMove(handler)
    return () => chart.unsubscribeCrosshairMove(handler)
    // `rebuildToken` (candleType) re-subscribes the crosshair handler on the fresh chart.
  }, [
    chartRef,
    overlaySeriesRef,
    spanPrimitiveRef,
    trendlinePrimitiveRef,
    divergencePricePrimitiveRef,
    drawnMarkers,
    rebuildToken,
    locale,
  ])
  return tooltip
}
