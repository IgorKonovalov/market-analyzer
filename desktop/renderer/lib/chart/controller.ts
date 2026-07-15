/**
 * ChartController — the imperative lightweight-charts core extracted from
 * CandlestickChart (Plan 0098 / ADR-0092). Owns the chart instance, the main +
 * always-on series (SeriesRegistry), the PaneRegistry, and the five main-series
 * primitives (PrimitiveHub), behind a declarative API the React component drives.
 * Plain TypeScript — no React import.
 *
 * The declarative API: `mount` / `setBars` / `dispose` (lifecycle), `setOverlays` /
 * `setPriceLines` / `setOscillators` (reconcilers), `setTrendlines` / `setIchimoku` /
 * `setDivergences` / `setMarkers` (primitive feeds), `restyle`, `setTimeframeAxis`
 * and `setQuote`. The work lives in focused sub-units (SeriesRegistry, PrimitiveHub,
 * OverlayReconciler, OscillatorPaneReconciler, restyle) so this facade only
 * delegates. The component instantiates the controller once and reuses it across
 * candle-type rebuilds (dispose → mount on the same instance); the exposed ref-object
 * identities are stable across that rebuild, matching the component refs the few
 * still-external hooks (OBV pane, fib/pivot, anchored VWAP, market structure) capture.
 */
import { ColorType, createChart } from 'lightweight-charts'
import type { IChartApi, ISeriesApi, Logical, UTCTimestamp } from 'lightweight-charts'

import type { Bar } from '../../types/sidecar/bar'
import type { QuoteResponse } from '../../types/sidecar/quote-response'
import type { CandleSeriesType } from '../chartStyle'
import { resolveChartStyle } from '../chartStyle'
import { monthlyTickMarkFormatter } from '../chartAxis'
import { timeframeDurationMs } from '../timeframes'
import type { ChartMarker } from '../markers'
import {
  PRICE_SCALE_ID,
  PRICE_SCALE_MARGINS,
  VOLUME_SCALE_ID,
  VOLUME_SCALE_MARGINS,
  chartColorsFrom,
  type MainSeries,
  type OverlayEntry,
} from '../chartSeries'
import { PaneRegistry } from '../panes'
import type { EffectiveTheme } from '../theme'
import type { DivergencePrimitive } from '../divergences'
import type { DrawingPrimitive } from '../drawings'
import type { IchimokuPrimitive } from '../ichimoku'
import type { PatternSpanPrimitive } from '../spans'
import type { TrendlinePrimitive } from '../trendlines'
import type { Divergence, OverlayKind, OverlaySpec, TrendlineSpec } from '../../types/events'
import type { MarketStructureResult } from '../marketStructure'
import type { HoverableLevel, StructureMarkerPoint } from '../tooltip'
import { applyRestyle } from './restyle'
import { ObvPaneReconciler } from './obvPane'
import {
  OverlayReconciler,
  type OverlayReconcileParams,
  type PriceLineReconcileParams,
} from './overlayReconciler'
import { OscillatorPaneReconciler, type OscillatorPaneEntry } from './oscillatorPanes'
import { PrimitiveHub } from './primitiveHub'
import type { Holder, MutRef } from './ref'
import { SeriesRegistry } from './seriesRegistry'

export interface MountOptions {
  candleType: CandleSeriesType
  theme: EffectiveTheme
}

export class ChartController {
  private readonly seriesRegistry = new SeriesRegistry()
  private readonly primitives = new PrimitiveHub()
  private readonly overlays = new OverlayReconciler()
  private readonly oscillators = new OscillatorPaneReconciler()
  private readonly obvPane = new ObvPaneReconciler()

  readonly chartRef: MutRef<IChartApi> = { current: null }
  readonly paneRegistryRef: MutRef<PaneRegistry> = { current: null }

  private containerEl: HTMLDivElement | null = null
  private candleType: CandleSeriesType = 'candles'
  private prevBars: Bar[] | null = null
  private prevFirstTs: number | null = null
  private barCountValue = 0

  // — Read handles for the still-React hooks. Removed as later phases fold each
  //   concern into the controller. Every getter returns a stable ref-object
  //   (created once in a sub-unit's field), so hook effects can capture them. —
  get seriesRef(): MutRef<MainSeries> {
    return this.seriesRegistry.mainRef
  }
  get volumeSeriesRef(): MutRef<ISeriesApi<'Histogram'>> {
    return this.seriesRegistry.volumeRef
  }
  get volumeMaSeriesRef(): MutRef<ISeriesApi<'Line'>> {
    return this.seriesRegistry.volumeMaRef
  }
  get vwapSeriesRef(): MutRef<ISeriesApi<'Line'>> {
    return this.seriesRegistry.vwapRef
  }
  get spanPrimitiveRef(): MutRef<PatternSpanPrimitive> {
    return this.primitives.spanRef
  }
  get trendlinePrimitiveRef(): MutRef<TrendlinePrimitive> {
    return this.primitives.trendlineRef
  }
  get ichimokuPrimitiveRef(): MutRef<IchimokuPrimitive> {
    return this.primitives.ichimokuRef
  }
  get divergencePricePrimitiveRef(): MutRef<DivergencePrimitive> {
    return this.primitives.divergencePriceRef
  }
  get drawingPrimitiveRef(): MutRef<DrawingPrimitive> {
    return this.primitives.drawingRef
  }
  /** ema/sma overlay series map — read by the test hook + restyle path. */
  get overlaySeriesRef(): Holder<Map<string, OverlayEntry>> {
    return this.overlays.overlaySeriesRef
  }
  /** Supertrend up/down series map — read by the restyle path. */
  get supertrendSeriesRef(): Holder<
    Map<string, { up: ISeriesApi<'Line'>; down: ISeriesApi<'Line'> }>
  > {
    return this.overlays.supertrendSeriesRef
  }
  /** Oscillator sub-panes map — read by `useDivergences` (fed per-pane). */
  get oscillatorPanesRef(): Holder<Map<string, OscillatorPaneEntry>> {
    return this.oscillators.panesRef
  }
  /** OBV line series (lazy) — read by the test hook. */
  get obvSeriesRef(): MutRef<ISeriesApi<'Line'>> {
    return this.obvPane.seriesRef
  }
  /** Bars currently set on the main series (0 when unmounted), for the test hook. */
  get barCount(): number {
    return this.seriesRegistry.mainRef.current !== null ? this.barCountValue : 0
  }

  /** Create the chart, its series, panes, scale margins and primitives. Idempotent
   * with `dispose`: the component's creation effect keys on `candleType`, so a
   * candle-type change disposes then re-mounts on this same instance (the series
   * type is fixed at creation). Resolves every style token to a concrete colour at
   * mount — lightweight-charts hands these to canvas APIs that don't resolve CSS
   * variables. */
  mount(container: HTMLDivElement, opts: MountOptions): void {
    this.candleType = opts.candleType
    this.containerEl = container
    const style = resolveChartStyle(container, opts.theme)
    const colors = chartColorsFrom(style)

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: colors.text,
      },
      grid: {
        vertLines: { color: colors.border },
        horzLines: { color: colors.border },
      },
      timeScale: {
        timeVisible: false,
        secondsVisible: false,
      },
      autoSize: true,
    })

    this.seriesRegistry.create(chart, opts.candleType, style, colors)
    // The pane registry owns every sub-pane below the price pane (the OBV pane and
    // the oscillator panes, both lazily reconciled by their hooks). Volume/VWAP stay
    // on pane 0.
    this.paneRegistryRef.current = new PaneRegistry(chart)
    // Candles occupy the upper band of the price pane; volume hugs its bottom.
    chart.priceScale(PRICE_SCALE_ID).applyOptions({ scaleMargins: PRICE_SCALE_MARGINS })
    chart.priceScale(VOLUME_SCALE_ID).applyOptions({ scaleMargins: VOLUME_SCALE_MARGINS })

    const series = this.seriesRegistry.mainRef.current
    if (series !== null) this.primitives.attach(series, container, colors)

    this.chartRef.current = chart
  }

  /** Push `bars` into the main + always-on series. A left-edge prepend (lazy paging,
   * Plan 0030) shifts the visible logical range right by the prepended count so the
   * viewport stays anchored; any other genuine data change fits the content; an
   * overlay/legend re-render that leaves `bars` identity intact does neither. */
  setBars(bars: Bar[]): void {
    const chart = this.chartRef.current
    const series = this.seriesRegistry.mainRef.current
    if (chart === null || series === null) return

    const newFirstMs = bars.length > 0 ? new Date(bars[0].event_ts).getTime() : null
    const prevFirstMs = this.prevFirstTs
    const grewOnLeft = prevFirstMs !== null && newFirstMs !== null && newFirstMs < prevFirstMs
    const rangeBeforePrepend = grewOnLeft ? chart.timeScale().getVisibleLogicalRange() : null

    this.seriesRegistry.setData(bars, this.candleType)
    this.barCountValue = bars.length

    const barsChanged = this.prevBars !== bars
    if (grewOnLeft && rangeBeforePrepend !== null && prevFirstMs !== null) {
      let prepended = 0
      for (const b of bars) {
        if (new Date(b.event_ts).getTime() < prevFirstMs) prepended += 1
        else break
      }
      chart.timeScale().setVisibleLogicalRange({
        from: (rangeBeforePrepend.from + prepended) as Logical,
        to: (rangeBeforePrepend.to + prepended) as Logical,
      })
    } else if (barsChanged) {
      chart.timeScale().fitContent()
    }
    this.prevBars = bars
    this.prevFirstTs = newFirstMs
  }

  /** Reconcile the price-pane overlay line-series families (ema/sma, supertrend,
   * bbands). No-op until mounted. The caller keys its effect on bars/overlays/hidden
   * (not the theme) — a created series takes its colour from the theme, but existing
   * series recolour in place via `restyle`. */
  setOverlays(params: OverlayReconcileParams): void {
    const chart = this.chartRef.current
    if (chart === null) return
    this.overlays.reconcile(chart, this.containerEl, params)
  }

  /** Reconcile the horizontal `price_line` overlays on the main series. Recolours in
   * place, so the caller keys its effect on the theme + styleVersion. */
  setPriceLines(params: PriceLineReconcileParams): void {
    const series = this.seriesRegistry.mainRef.current
    if (series === null) return
    this.overlays.reconcilePriceLines(series, this.containerEl, params)
  }

  /** Reconcile the oscillator sub-panes against the desired + divergence-required
   * oscillator set. No-op until the chart + pane registry exist. */
  setOscillators(params: {
    bars: Bar[]
    overlays: ReadonlyArray<OverlaySpec> | undefined
    hidden: ReadonlySet<string>
    requiredKinds: ReadonlySet<OverlayKind>
  }): void {
    const chart = this.chartRef.current
    const registry = this.paneRegistryRef.current
    if (chart === null || registry === null) return
    this.oscillators.reconcile(chart, registry, params)
  }

  /** Feed the trendline primitive its specs + hovered legend group. */
  setTrendlines(specs: ReadonlyArray<TrendlineSpec>, highlightKey: string | null): void {
    this.primitives.setTrendlines(this.containerEl, specs, highlightKey)
  }

  /** Feed the Ichimoku primitive its geometries + reserve trailing axis space. */
  setIchimoku(params: {
    bars: Bar[]
    overlays: ReadonlyArray<OverlaySpec> | undefined
    hidden: ReadonlySet<string>
  }): void {
    this.primitives.setIchimoku(
      this.chartRef.current,
      this.containerEl,
      params.bars,
      params.overlays,
      params.hidden,
    )
  }

  /** Feed the price + OBV + oscillator-pane divergence primitives their segments. */
  setDivergences(divergences: ReadonlyArray<Divergence>): void {
    this.primitives.setDivergences(
      this.containerEl,
      divergences,
      this.obvPane.divergencePrimitiveRef.current,
      this.oscillators.panesRef.current,
    )
  }

  /** Reconcile the lazy OBV sub-pane (create/remove + line data). */
  setObv(params: {
    bars: Bar[]
    hidden: ReadonlySet<string>
    divergences: ReadonlyArray<Divergence>
    theme: EffectiveTheme
  }): void {
    this.obvPane.reconcile(
      this.chartRef.current,
      this.containerEl,
      this.paneRegistryRef.current,
      params,
    )
  }

  /** Reconcile the fib/pivot horizontal price lines; returns the drawn levels for
   * the hover tooltip. */
  setStructureLevels(params: {
    bars: Bar[]
    overlays: ReadonlyArray<OverlaySpec> | undefined
    hidden: ReadonlySet<string>
  }): HoverableLevel[] {
    const series = this.seriesRegistry.mainRef.current
    if (series === null) return []
    return this.overlays.reconcileStructureLevels(series, params)
  }

  /** Reconcile the anchored-VWAP line series. */
  setAnchoredVwap(params: {
    bars: Bar[]
    overlays: ReadonlyArray<OverlaySpec> | undefined
    hidden: ReadonlySet<string>
  }): void {
    const chart = this.chartRef.current
    if (chart === null) return
    this.overlays.reconcileAnchoredVwap(chart, params)
  }

  /** Draw the market-structure labels/glyphs; returns the drawn points for the
   * hover tooltip. */
  setMarketStructure(params: {
    structure: MarketStructureResult
    bars: Bar[]
    hidden: ReadonlySet<string>
    theme: EffectiveTheme
  }): StructureMarkerPoint[] {
    return this.primitives.setMarketStructure(
      this.seriesRegistry.mainRef.current,
      this.containerEl,
      params,
    )
  }

  /** Feed the candlestick markers plugin + the pattern-span band. */
  setMarkers(params: {
    drawnMarkers: ChartMarker[]
    clickedBarTs: string | null
    highlightGroup: string | null
    theme: EffectiveTheme
  }): void {
    this.primitives.setMarkers(this.seriesRegistry.mainRef.current, this.containerEl, params)
  }

  /** Re-apply the existing chart's colours + widths in place (no remount). */
  restyle(theme: EffectiveTheme): void {
    applyRestyle({
      chart: this.chartRef.current,
      mainSeries: this.seriesRegistry.mainRef.current,
      container: this.containerEl,
      candleType: this.candleType,
      theme,
      volumeSeries: this.seriesRegistry.volumeRef.current,
      volumeMaSeries: this.seriesRegistry.volumeMaRef.current,
      vwapSeries: this.seriesRegistry.vwapRef.current,
      obvSeries: this.obvPane.seriesRef.current,
      overlaySeries: this.overlays.overlaySeriesRef.current,
      supertrendSeries: this.overlays.supertrendSeriesRef.current,
    })
  }

  /** The `1mo` timeframe gets month/year tick marks; every other timeframe the
   * library default. Re-applied on a rebuild (the fresh chart needs the formatter). */
  setTimeframeAxis(timeframe: string | undefined): void {
    const chart = this.chartRef.current
    if (chart === null) return
    chart.applyOptions({
      timeScale: {
        tickMarkFormatter: timeframe === '1mo' ? monthlyTickMarkFormatter : undefined,
      },
    })
  }

  /** Live forming-bar update (Plan 0049 phase 10): feed the already-polled `/quote`
   * into the CURRENT bar via `series.update()`, but only when the quote's `as_of`
   * falls within the latest bar's period — never rewrite a closed bar nor fabricate
   * a new one. No lookahead: this is the live current bar, not historical replay. */
  setQuote(
    quote: QuoteResponse | null | undefined,
    bars: Bar[],
    timeframe: string | undefined,
  ): void {
    const series = this.seriesRegistry.mainRef.current
    if (series === null || !quote || bars.length === 0) return
    const periodMs = timeframeDurationMs(timeframe)
    if (periodMs === null) return
    const last = bars[bars.length - 1]
    const lastStartMs = new Date(last.event_ts).getTime()
    const asOfMs = new Date(quote.as_of).getTime()
    if (asOfMs < lastStartMs || asOfMs >= lastStartMs + periodMs) return
    const time = Math.floor(lastStartMs / 1000) as UTCTimestamp
    if (this.candleType === 'line' || this.candleType === 'area') {
      ;(series as ISeriesApi<'Line'>).update({ time, value: quote.price })
    } else {
      ;(series as ISeriesApi<'Candlestick'>).update({
        time,
        open: last.open,
        high: Math.max(last.high, quote.price),
        low: Math.min(last.low, quote.price),
        close: quote.price,
      })
    }
  }

  /** Dispose the chart (which disposes its series, panes and primitives) and drop
   * all bookkeeping. Symmetric with `mount` — a mount → dispose → mount round-trip
   * leaves no stranded primitive or leaked chart. */
  dispose(): void {
    this.chartRef.current?.remove()
    this.chartRef.current = null
    this.paneRegistryRef.current = null
    this.containerEl = null
    this.seriesRegistry.clear()
    this.primitives.clear()
    this.overlays.clear()
    this.oscillators.clear()
    this.obvPane.clear()
    this.prevBars = null
    this.prevFirstTs = null
    this.barCountValue = 0
  }
}
