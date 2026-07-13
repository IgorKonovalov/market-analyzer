/**
 * Market-structure marker reconcile (Plan 0092 phase 6, ADR-0084).
 *
 * Draws the price-action structure on the candlestick series: HH/HL/LH/LL labels
 * at the confirmed swing pivots (arrow above a high, below a low) and BOS/CHoCH
 * glyphs at their events. Its own `createSeriesMarkers` plugin (independent of the
 * candlestick-pattern markers `useChartMarkers` owns), keyed on series identity so
 * a candle-type rebuild re-attaches to the fresh series. A CHoCH stands apart in
 * amber (a character change is the notable event); the rest colour by structural
 * direction. Toggled off as one layer via `MARKET_STRUCTURE_LAYER_ID` in `hidden`.
 *
 * MUST be called after the chart-creation effect so `seriesRef` is populated.
 * `styleVersion`/`rebuildToken` re-run it after a restyle / rebuild.
 */
import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'
import { createSeriesMarkers } from 'lightweight-charts'
import type { ISeriesMarkersPluginApi, SeriesMarker, Time, UTCTimestamp } from 'lightweight-charts'

import { chartColorsFrom, type MainSeries } from '../lib/chartSeries'
import { resolveChartStyle } from '../lib/chartStyle'
import type { MarketStructureResult } from '../lib/marketStructure'
import type { EffectiveTheme } from '../lib/theme'
import type { Bar } from '../types/sidecar/bar'

/** The single legend layer id for the whole market-structure marker set. */
export const MARKET_STRUCTURE_LAYER_ID = 'structure'
/** A change-of-character stands apart from the trend colours — amber. */
export const CHOCH_MARKER_COLOR = '#f59e0b'

function toUtc(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp
}

export interface UseMarketStructureMarkersParams {
  structure: MarketStructureResult
  bars: Bar[]
  hidden: ReadonlySet<string>
  effectiveTheme: EffectiveTheme
  styleVersion: number
  rebuildToken: unknown
}

export function useMarketStructureMarkers(
  seriesRef: RefObject<MainSeries | null>,
  containerRef: RefObject<HTMLDivElement>,
  {
    structure,
    bars,
    hidden,
    effectiveTheme,
    styleVersion,
    rebuildToken,
  }: UseMarketStructureMarkersParams,
): void {
  const pluginRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const pluginSeriesRef = useRef<MainSeries | null>(null)

  useEffect(() => {
    const series = seriesRef.current
    const container = containerRef.current
    if (!series || !container) return
    const colors = chartColorsFrom(resolveChartStyle(container, effectiveTheme))

    const markers: SeriesMarker<UTCTimestamp>[] = []
    if (!hidden.has(MARKET_STRUCTURE_LAYER_ID)) {
      for (const { pivot, label } of structure.labeledPivots) {
        const bullish = label === 'HH' || label === 'HL'
        markers.push({
          time: toUtc(pivot.ts),
          position: pivot.kind === 'high' ? 'aboveBar' : 'belowBar',
          shape: pivot.kind === 'high' ? 'arrowDown' : 'arrowUp',
          color: bullish ? colors.markerBullish : colors.markerBearish,
          text: label,
        })
      }
      for (const event of structure.events) {
        const bar = bars[event.barIndex]
        if (bar === undefined) continue
        const trendColor =
          event.direction === 'bullish' ? colors.markerBullish : colors.markerBearish
        markers.push({
          time: toUtc(bar.event_ts),
          position: event.direction === 'bullish' ? 'aboveBar' : 'belowBar',
          shape: 'circle',
          color: event.kind === 'CHoCH' ? CHOCH_MARKER_COLOR : trendColor,
          text: event.kind,
        })
      }
    }
    // The markers plugin requires ascending time order.
    markers.sort((a, b) => (a.time as number) - (b.time as number))

    if (pluginRef.current === null || pluginSeriesRef.current !== series) {
      // First run, or the series was rebuilt (candle-type change): attach a fresh
      // plugin. The old plugin (if any) died with its series.
      pluginRef.current = createSeriesMarkers(series)
      pluginSeriesRef.current = series
    }
    pluginRef.current.setMarkers(markers)
  }, [seriesRef, containerRef, structure, bars, hidden, effectiveTheme, styleVersion, rebuildToken])
}
