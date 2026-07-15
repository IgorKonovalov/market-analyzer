/**
 * PrimitiveHub — the five ISeriesPrimitives that ride the MAIN price series for the
 * chart's whole life (pattern-span band, trendline overlay, Ichimoku cloud,
 * price-pane divergence, freeform drawings), plus the candlestick markers plugin.
 * Attached once at chart creation so they ride the live series and are disposed by
 * `chart.remove()` — the Plan 0064 fix that kept a hook-attached primitive from
 * stranding on a discarded StrictMode chart.
 *
 * Beyond attach/lifecycle (Plan 0098 phase 1), this owns the per-primitive FEED
 * methods folded out of useTrendlines / useIchimokuSeries / useDivergences /
 * useChartMarkers (Plan 0098 phase 3) — no behaviour change. Pure imperative wiring;
 * no React.
 */
import { createSeriesMarkers } from 'lightweight-charts'
import type {
  IChartApi,
  ISeriesMarkersPluginApi,
  SeriesMarker,
  Time,
  UTCTimestamp,
} from 'lightweight-charts'

import { chartColorsFrom, type ChartColors, type MainSeries } from '../chartSeries'
import { resolveChartStyle } from '../chartStyle'
import {
  DivergencePrimitive,
  divergenceOscillatorToPaneKind,
  fallbackDivergenceColors,
  readDivergenceColors,
} from '../divergences'
import { DrawingPrimitive } from '../drawings'
import {
  IchimokuPrimitive,
  computeIchimokuGeometry,
  ichimokuPeriods,
  readIchimokuColors,
  type IchimokuGeometry,
} from '../ichimoku'
import { annotationsToMarkers, type ChartMarker } from '../markers'
import { overlayLayerId } from '../overlays'
import { PatternSpanPrimitive, markersToSpans } from '../spans'
import { TrendlinePrimitive, readTrendlineColors } from '../trendlines'
import type { EffectiveTheme } from '../theme'
import type { Bar } from '../../types/sidecar/bar'
import type { Divergence, OverlaySpec, TrendlineSpec } from '../../types/events'
import type { OscillatorPaneEntry } from './oscillatorPanes'
import type { MutRef } from './ref'

export interface MarkerFeedParams {
  drawnMarkers: ChartMarker[]
  clickedBarTs: string | null
  highlightGroup: string | null
  theme: EffectiveTheme
}

export class PrimitiveHub {
  readonly spanRef: MutRef<PatternSpanPrimitive> = { current: null }
  readonly trendlineRef: MutRef<TrendlinePrimitive> = { current: null }
  readonly ichimokuRef: MutRef<IchimokuPrimitive> = { current: null }
  readonly divergencePriceRef: MutRef<DivergencePrimitive> = { current: null }
  readonly drawingRef: MutRef<DrawingPrimitive> = { current: null }

  // v5 markers plugin (replaces the removed ISeriesApi.setMarkers). Held across
  // feeds; recreated when the main series is rebuilt (candle-type switch).
  private markersPlugin: ISeriesMarkersPluginApi<Time> | null = null
  private markersSeries: MainSeries | null = null
  // Whether a non-default Ichimoku `rightOffset` is currently reserved, so it resets
  // exactly once when the last Ichimoku overlay goes away.
  private ichimokuReserved = false

  /** Create + attach all five primitives to the freshly-created main series. Order
   * is the creation-effect order (span → trendline → ichimoku → price-divergence →
   * drawing); the component's candle-type-rebuild test asserts exactly five attaches
   * on the main series. */
  attach(series: MainSeries, container: HTMLDivElement, colors: ChartColors): void {
    const spanPrimitive = new PatternSpanPrimitive({
      bullish: colors.markerBullish,
      bearish: colors.markerBearish,
      neutral: colors.markerNeutral,
    })
    series.attachPrimitive(spanPrimitive)
    this.spanRef.current = spanPrimitive

    const trendlinePrimitive = new TrendlinePrimitive(readTrendlineColors(container))
    series.attachPrimitive(trendlinePrimitive)
    this.trendlineRef.current = trendlinePrimitive

    const ichimokuPrimitive = new IchimokuPrimitive(readIchimokuColors(container))
    series.attachPrimitive(ichimokuPrimitive)
    this.ichimokuRef.current = ichimokuPrimitive

    const divergencePricePrimitive = new DivergencePrimitive(
      'price',
      readDivergenceColors(container),
    )
    series.attachPrimitive(divergencePricePrimitive)
    this.divergencePriceRef.current = divergencePricePrimitive

    const drawingPrimitive = new DrawingPrimitive()
    series.attachPrimitive(drawingPrimitive)
    this.drawingRef.current = drawingPrimitive
  }

  /** Feed the trendline primitive its specs, theme-resolved colours and the hovered
   * legend group. Recolours in place — the caller keys its effect on the theme. */
  setTrendlines(
    container: HTMLDivElement | null,
    specs: ReadonlyArray<TrendlineSpec>,
    highlightKey: string | null,
  ): void {
    const primitive = this.trendlineRef.current
    if (primitive === null || container === null) return
    primitive.setColors(readTrendlineColors(container))
    primitive.setTrendlines(specs)
    primitive.setHighlightedGroup(highlightKey)
  }

  /** Feed the Ichimoku primitive its geometries + colours, and reserve trailing axis
   * space so the projected cloud lands on-screen (reset when no overlay is shown). */
  setIchimoku(
    chart: IChartApi | null,
    container: HTMLDivElement | null,
    bars: Bar[],
    overlays: ReadonlyArray<OverlaySpec> | undefined,
    hidden: ReadonlySet<string>,
  ): void {
    const primitive = this.ichimokuRef.current
    if (primitive === null || chart === null || container === null) return
    primitive.setColors(readIchimokuColors(container))

    const geometries: IchimokuGeometry[] = []
    let maxDisplacement = 0
    let hasSpec = false
    for (const spec of overlays ?? []) {
      if (spec.kind !== 'ichimoku') continue
      hasSpec = true
      if (hidden.has(overlayLayerId(spec))) continue
      geometries.push(computeIchimokuGeometry(bars, spec))
      maxDisplacement = Math.max(maxDisplacement, ichimokuPeriods(spec).displacement)
    }
    primitive.setGeometries(geometries)

    if (hasSpec) {
      const offset = geometries.length > 0 ? maxDisplacement : 0
      chart.timeScale().applyOptions({ rightOffset: offset })
      this.ichimokuReserved = offset > 0
    } else if (this.ichimokuReserved) {
      chart.timeScale().applyOptions({ rightOffset: 0 })
      this.ichimokuReserved = false
    }
  }

  /** Feed the price / OBV / oscillator-pane divergence primitives their segments +
   * colours. The OBV primitive is owned by `useObvPane` (still external) and passed
   * in; each oscillator pane's primitive is owned by its reconciler entry. */
  setDivergences(
    container: HTMLDivElement | null,
    divergences: ReadonlyArray<Divergence>,
    obvPrimitive: DivergencePrimitive | null,
    oscillatorPanes: Map<string, OscillatorPaneEntry>,
  ): void {
    const colors = container ? readDivergenceColors(container) : fallbackDivergenceColors()

    const pricePrimitive = this.divergencePriceRef.current
    if (pricePrimitive) {
      pricePrimitive.setColors(colors)
      pricePrimitive.setDivergences(divergences)
    }
    if (obvPrimitive) {
      obvPrimitive.setColors(colors)
      obvPrimitive.setDivergences(divergences.filter((d) => d.oscillator === 'obv'))
    }
    for (const entry of oscillatorPanes.values()) {
      entry.divergencePrimitive.setColors(colors)
      entry.divergencePrimitive.setDivergences(
        divergences.filter((d) => divergenceOscillatorToPaneKind(d.oscillator) === entry.kind),
      )
    }
  }

  /** Feed the candlestick markers plugin + the pattern-span band: only the enabled
   * groups' markers/spans, glyph-only, with the clicked-bar affordance and hover
   * emphasis. Recreates the markers plugin when the main series is rebuilt. */
  setMarkers(
    series: MainSeries | null,
    container: HTMLDivElement | null,
    { drawnMarkers, clickedBarTs, highlightGroup, theme }: MarkerFeedParams,
  ): void {
    if (series === null || container === null) return
    const colors = chartColorsFrom(resolveChartStyle(container, theme))
    const markerColors = {
      bullish: colors.markerBullish,
      bearish: colors.markerBearish,
      neutral: colors.markerNeutral,
    }

    const base = annotationsToMarkers(drawnMarkers, markerColors, {
      includeText: false,
      highlightGroupKey: highlightGroup,
    })
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
    if (this.markersPlugin === null || this.markersSeries !== series) {
      // First feed, or the series was rebuilt (candle-type change): attach a fresh
      // plugin to the current series. The old plugin (if any) died with its series.
      this.markersPlugin = createSeriesMarkers(series)
      this.markersSeries = series
    }
    this.markersPlugin.setMarkers(markers)

    // The span band gates identically to the markers (Plan 0071 phase 2).
    const span = this.spanRef.current
    if (span) {
      span.setColors(markerColors)
      span.setSpans(markersToSpans(drawnMarkers))
      span.setVisible(true)
    }
  }

  /** Drop bookkeeping after `chart.remove()` detached the primitives + markers. */
  clear(): void {
    this.spanRef.current = null
    this.trendlineRef.current = null
    this.ichimokuRef.current = null
    this.divergencePriceRef.current = null
    this.drawingRef.current = null
    this.markersPlugin = null
    this.markersSeries = null
    this.ichimokuReserved = false
  }
}
