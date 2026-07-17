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
import type {
  ISeriesApi,
  LineData,
  IChartApi,
  MouseEventParams,
  UTCTimestamp,
} from 'lightweight-charts'

import type { MainSeries, OverlayEntry } from '../lib/chartSeries'
import type { ChartMarker } from '../lib/markers'
import { PatternSpanPrimitive, markerHighlightSpan } from '../lib/spans'
import { TrendlinePrimitive } from '../lib/trendlines'
import { DivergencePrimitive } from '../lib/divergences'
import type { DrawingPrimitive } from '../lib/drawings'
import type { IchimokuPrimitive } from '../lib/ichimoku'
import {
  type HoverableLevel,
  type OverlayReading,
  type StructureMarkerPoint,
  type TooltipContent,
  divergenceTooltipText,
  drawingAdvisoryTooltip,
  ichimokuTooltipLines,
  levelTooltipText,
  nearestLevelAtY,
  overlayLabel,
  structureTooltipText,
  supertrendReading,
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
  /** The drawn fib/pivot structure levels for the nearest-level-by-Y hover
   * (Plan 0105 phase 6 / ADR-0100 rule 3) — with the main-series ref that maps
   * a level price to its pane pixel. Both optional: a chart without structure
   * overlays passes nothing and skips the lookup. */
  structureLevels?: ReadonlyArray<HoverableLevel>
  seriesRef?: RefObject<MainSeries | null>
  /** The DRAWN market-structure markers (HH/HL/LH/LL + BOS/CHoCH) for the
   * time-keyed structure hover (Plan 0105 phase 7) — a toggled-off layer
   * publishes none, so it shows no hover (the `drawnMarkers` gate, mirrored). */
  structureMarkers?: ReadonlyArray<StructureMarkerPoint>
  /** The supertrend up/down masked series, for the flip line's per-bar hover
   * reading (value + active band). Optional: charts without supertrend skip it. */
  supertrendSeriesRef?: RefObject<Map<string, { up: ISeriesApi<'Line'>; down: ISeriesApi<'Line'> }>>
  /** The ichimoku primitive, for the near-line / inside-cloud hover read-out. */
  ichimokuPrimitiveRef?: RefObject<IchimokuPrimitive | null>
  /** Visible agent `price_line` S/R levels for the nearest-level-by-Y hover —
   * merged with the fib/pivot structure levels so hovering a resistance line
   * shows its label + price. */
  priceLineLevels?: ReadonlyArray<HoverableLevel>
  rebuildToken: unknown
}

export function useChartTooltip(
  chartRef: RefObject<IChartApi | null>,
  overlaySeriesRef: RefObject<Map<string, OverlayEntry>>,
  spanPrimitiveRef: RefObject<PatternSpanPrimitive | null>,
  trendlinePrimitiveRef: RefObject<TrendlinePrimitive | null>,
  divergencePricePrimitiveRef: RefObject<DivergencePrimitive | null>,
  drawingPrimitiveRef: RefObject<DrawingPrimitive | null>,
  {
    drawnMarkers,
    structureLevels,
    seriesRef,
    structureMarkers,
    supertrendSeriesRef,
    ichimokuPrimitiveRef,
    priceLineLevels,
    rebuildToken,
  }: UseChartTooltipParams,
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
        // Supertrend's flip line: whichever masked series (up/down) carries a
        // value at the hovered bar names the active band.
        for (const pair of supertrendSeriesRef?.current?.values() ?? []) {
          const upValue = (param.seriesData.get(pair.up) as LineData | undefined)?.value
          const downValue = (param.seriesData.get(pair.down) as LineData | undefined)?.value
          if (typeof upValue === 'number') readings.push(supertrendReading(upValue, 'up'))
          else if (typeof downValue === 'number')
            readings.push(supertrendReading(downValue, 'down'))
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
      // Nearest drawn fib/pivot level by crosshair-Y (Plan 0105 phase 6 /
      // ADR-0100 rule 3): reuse the price lines already drawn — no per-level
      // hit-test primitives. `priceToCoordinate` maps in the price pane, the
      // pane every structure level lives on.
      const mainSeries = seriesRef?.current ?? null
      const hoverableLevels = [...(structureLevels ?? []), ...(priceLineLevels ?? [])]
      const hoveredLevel =
        mainSeries !== null && hoverableLevels.length > 0
          ? nearestLevelAtY(param.point.y, hoverableLevels, (price) => {
              const coordinate = mainSeries.priceToCoordinate?.(price)
              return coordinate == null ? null : coordinate
            })
          : null
      const levels = hoveredLevel ? [levelTooltipText(hoveredLevel)] : []
      // Ichimoku under the cursor: near one of the five lines or inside the
      // cloud → the stance line + the numeric readings at that logical position.
      const ichimokuReadout =
        ichimokuPrimitiveRef?.current?.hoverReadings(param.point.x, param.point.y) ?? null
      const ichimokuContent =
        ichimokuReadout !== null ? ichimokuTooltipLines(ichimokuReadout) : null
      const ichimoku = ichimokuContent?.stance != null ? [ichimokuContent.stance] : []
      if (ichimokuContent !== null) readings.push(...ichimokuContent.readings)
      // Agent-position advisory under the cursor (Plan 0104 phase 4): the drawing
      // primitive returns the hovered spec; an agent position with a rationale
      // yields the `Advisory — <rationale>` line (ADR-0029/0099).
      const hoveredDrawing =
        drawingPrimitiveRef.current?.hoveredDrawingSpec(param.point.x, param.point.y) ?? null
      const advisoryLine = hoveredDrawing ? drawingAdvisoryTooltip(hoveredDrawing, locale) : null
      const advisory = advisoryLine !== null ? [advisoryLine] : []
      // Market-structure markers on the hovered bar (Plan 0105 phase 7): the
      // same time-keyed match candlestick markers use, glossary-backed content.
      const structures =
        param.time !== undefined && structureMarkers !== undefined
          ? structureMarkers
              .filter((m) => m.time === param.time)
              .map((m) => structureTooltipText(m.label, locale))
          : []
      const markers = timeContent?.markers ?? []
      // `readings` (not `timeContent.overlays`) so the ichimoku readings survive
      // past the last bar, where `param.time` is undefined but the displaced
      // cloud still renders.
      const overlays = readings
      const markerMeaning = timeContent?.markerMeaning
      if (
        markers.length === 0 &&
        overlays.length === 0 &&
        trendlines.length === 0 &&
        divergences.length === 0 &&
        levels.length === 0 &&
        structures.length === 0 &&
        advisory.length === 0 &&
        ichimoku.length === 0
      ) {
        setTooltip(null)
        return
      }
      setTooltip({
        content: {
          markers,
          overlays,
          trendlines,
          divergences,
          levels,
          structures,
          advisory,
          ichimoku,
          markerMeaning,
        },
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
    drawingPrimitiveRef,
    drawnMarkers,
    structureLevels,
    seriesRef,
    structureMarkers,
    supertrendSeriesRef,
    ichimokuPrimitiveRef,
    priceLineLevels,
    rebuildToken,
    locale,
  ])
  return tooltip
}
