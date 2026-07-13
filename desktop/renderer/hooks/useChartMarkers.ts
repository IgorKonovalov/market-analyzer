/**
 * Candlestick-marker + pattern-span reconcile (Plan 0049 phases 7 & 10 / Plan
 * 0071 phase 2 — lifted verbatim out of `CandlestickChart` in the Plan 0072
 * phase 8 decomposition, no behaviour change).
 *
 * Two effects, one concern (what the candlestick sweep draws on the candles):
 *   1. Set the arrow markers — only the ENABLED groups' markers (`drawnMarkers`),
 *      plus the clicked-bar affordance, glyph-only (labels ride the hover
 *      tooltip). A hovered legend group emphasises its markers.
 *   2. Feed the pattern-span band the same `drawnMarkers` (so markers and spans
 *      gate identically) and the theme-resolved direction colours.
 *
 * Both recolour in place on a theme flip; `styleVersion`/`rebuildToken` re-run
 * them after a chart restyle / rebuild. MUST be called after the chart-creation
 * effect so `seriesRef` / `spanPrimitiveRef` are populated on mount.
 */
import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'
import { createSeriesMarkers } from 'lightweight-charts'
import type { ISeriesMarkersPluginApi, SeriesMarker, Time, UTCTimestamp } from 'lightweight-charts'

import { chartColorsFrom, type MainSeries } from '../lib/chartSeries'
import { resolveChartStyle } from '../lib/chartStyle'
import { annotationsToMarkers, type ChartMarker } from '../lib/markers'
import { PatternSpanPrimitive, markersToSpans } from '../lib/spans'
import type { EffectiveTheme } from '../lib/theme'

export interface UseChartMarkersParams {
  /** The markers actually drawn (master on ⊗ group enabled — Plan 0071 phase 2). */
  drawnMarkers: ChartMarker[]
  /** The agent-clicked bar's ISO ts, or null — draws a neutral circle affordance. */
  clickedBarTs: string | null
  /** The hovered candlestick group's key — its markers grow, the rest fade. */
  highlightedCandleGroup: string | null
  effectiveTheme: EffectiveTheme
  styleVersion: number
  rebuildToken: unknown
}

export function useChartMarkers(
  seriesRef: RefObject<MainSeries | null>,
  containerRef: RefObject<HTMLDivElement>,
  spanPrimitiveRef: RefObject<PatternSpanPrimitive | null>,
  {
    drawnMarkers,
    clickedBarTs,
    highlightedCandleGroup,
    effectiveTheme,
    styleVersion,
    rebuildToken,
  }: UseChartMarkersParams,
): void {
  // v5 removed `ISeriesApi.setMarkers`; markers are a plugin now
  // (`createSeriesMarkers`). Hold one plugin instance across renders and update it
  // in place. A candle-type rebuild recreates the main series, so the plugin is
  // keyed on series identity — a new series means the old plugin died with it.
  const markersPluginRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const markersSeriesRef = useRef<MainSeries | null>(null)

  useEffect(() => {
    const series = seriesRef.current
    const container = containerRef.current
    if (!series || !container) return
    const colors = chartColorsFrom(resolveChartStyle(container, effectiveTheme))
    // Draw-on-select (Plan 0071 phase 2): only the enabled groups' markers paint,
    // so the sweep never dumps all N at once. A hovered legend group emphasises
    // its markers.
    const base = annotationsToMarkers(
      drawnMarkers,
      {
        bullish: colors.markerBullish,
        bearish: colors.markerBearish,
        neutral: colors.markerNeutral,
      },
      // Glyph-only on the canvas; the label shows in the backed hover tooltip
      // (Plan 0049 phase 12 — no bare overlapping text over the candles).
      { includeText: false, highlightGroupKey: highlightedCandleGroup },
    )
    let markers = base
    if (clickedBarTs !== null) {
      const time = Math.floor(new Date(clickedBarTs).getTime() / 1000) as UTCTimestamp
      const clicked: SeriesMarker<UTCTimestamp> = {
        time,
        position: 'aboveBar',
        shape: 'circle',
        color: colors.markerClicked,
        text: clickedBarTs.slice(0, 10),
      }
      // The markers plugin requires ascending time order.
      markers = [...base, clicked].sort((a, b) => (a.time as number) - (b.time as number))
    }
    if (markersPluginRef.current === null || markersSeriesRef.current !== series) {
      // First run, or the series was rebuilt (candle-type change): attach a fresh
      // plugin to the current series. The old plugin (if any) died with its series.
      markersPluginRef.current = createSeriesMarkers(series)
      markersSeriesRef.current = series
    }
    // Always drive the markers through the plugin (one uniform path).
    markersPluginRef.current.setMarkers(markers)
  }, [
    seriesRef,
    containerRef,
    drawnMarkers,
    clickedBarTs,
    effectiveTheme,
    styleVersion,
    rebuildToken,
    highlightedCandleGroup,
  ])

  // Multi-bar pattern span band (Plan 0049 phase 7): feed the span primitive the
  // current spans, theme-resolved direction colours, and the legend visibility.
  // The primitive redraws via `requestUpdate`; the band tracks pan/zoom for free.
  // Recolours in place on a theme flip — no remount (the primitive persists).
  useEffect(() => {
    const primitive = spanPrimitiveRef.current
    const container = containerRef.current
    if (!primitive || !container) return
    const colors = chartColorsFrom(resolveChartStyle(container, effectiveTheme))
    primitive.setColors({
      bullish: colors.markerBullish,
      bearish: colors.markerBearish,
      neutral: colors.markerNeutral,
    })
    // Spans gate identically to the markers (Plan 0071 phase 2): only the enabled
    // groups' spans draw (master off / group disabled → `drawnMarkers` excludes
    // them → no span). The standalone "Pattern spans" row folds into the master.
    primitive.setSpans(markersToSpans(drawnMarkers))
    primitive.setVisible(true)
    // `rebuildToken` re-feeds the freshly-attached span primitive after a rebuild.
  }, [spanPrimitiveRef, containerRef, drawnMarkers, effectiveTheme, styleVersion, rebuildToken])
}
