/**
 * OverlayReconciler — the price-pane overlay families that add/reuse/remove line
 * series (and horizontal price lines) as the agent's overlays + the legend's hidden
 * set change: single-line ema/sma, the two-masked-series supertrend, the three-line
 * Bollinger Bands, and the `price_line` S/R levels. Folded verbatim out of the
 * `useOverlaySeries` / `useSupertrendSeries` / `useBbandsSeries` / `usePriceLines`
 * hooks (Plan 0098 phase 2, ADR-0092) — no behaviour change. Pure imperative wiring
 * over lightweight-charts; no React.
 *
 * The line-series families (`reconcile`) resolve a CREATED series' initial colour
 * off the passed theme; existing series recolour in place via the restyle path, so
 * the caller keys its effect on bars/overlays/hidden (not the theme). Price lines
 * (`reconcilePriceLines`) recolour in place, so their caller does key on the theme.
 * Oscillators, ichimoku and the price-structure families (fib/pivot/anchored-VWAP)
 * draw elsewhere and are skipped here, exactly as the hooks did.
 */
import { LineSeries, LineStyle } from 'lightweight-charts'
import type { IChartApi, IPriceLine, ISeriesApi } from 'lightweight-charts'

import {
  DEFAULT_OVERLAY_LINE_WIDTH,
  chartColorsFrom,
  overlayKey,
  overlayStyleColor,
  overlayStyleWidth,
  type MainSeries,
  type OverlayEntry,
} from '../chartSeries'
import { resolveChartStyle } from '../chartStyle'
import { computeBbands } from '../indicators'
import { DEFAULT_MARKER_COLORS } from '../markers'
import {
  BBANDS_LINE_COLOR,
  computeOverlayData,
  computeSupertrend,
  isOscillatorOverlay,
  isStructureOverlay,
  isSupportedOverlay,
  overlayColorFor,
  overlayLayerId,
  supertrendBands,
} from '../overlays'
import { priceLineColor, priceLineId } from '../priceLines'
import type { EffectiveTheme } from '../theme'
import type { Bar } from '../../types/sidecar/bar'
import type { OverlaySpec } from '../../types/events'
import type { Holder } from './ref'

type SupertrendPair = { up: ISeriesApi<'Line'>; down: ISeriesApi<'Line'> }
type BbandsTriple = {
  upper: ISeriesApi<'Line'>
  middle: ISeriesApi<'Line'>
  lower: ISeriesApi<'Line'>
}

export interface OverlayReconcileParams {
  bars: Bar[]
  overlays: ReadonlyArray<OverlaySpec> | undefined
  hidden: ReadonlySet<string>
  theme: EffectiveTheme
}

export interface PriceLineReconcileParams {
  overlays: ReadonlyArray<OverlaySpec> | undefined
  hidden: ReadonlySet<string>
  theme: EffectiveTheme
}

/** Defaults mirror the pydantic `bbands` descriptor + the Python `bollinger`
 * signature: period 20, std-dev multiplier `k` 2.0 (Plan 0082 ph1). */
const BBANDS_DEFAULT_PERIOD = 20
const BBANDS_DEFAULT_K = 2

export class OverlayReconciler {
  readonly overlaySeriesRef: Holder<Map<string, OverlayEntry>> = { current: new Map() }
  readonly supertrendSeriesRef: Holder<Map<string, SupertrendPair>> = { current: new Map() }
  readonly bbandsSeriesRef: Holder<Map<string, BbandsTriple>> = { current: new Map() }
  readonly priceLinesRef: Holder<Map<string, IPriceLine>> = { current: new Map() }

  /** Reconcile the price-pane line-series families (ema/sma, supertrend, bbands). */
  reconcile(
    chart: IChartApi,
    container: HTMLDivElement | null,
    params: OverlayReconcileParams,
  ): void {
    this.reconcileLines(chart, container, params)
    this.reconcileSupertrend(chart, container, params)
    this.reconcileBbands(chart, params)
  }

  private reconcileLines(
    chart: IChartApi,
    container: HTMLDivElement | null,
    { bars, overlays, hidden, theme }: OverlayReconcileParams,
  ): void {
    const overlaySeries = this.overlaySeriesRef.current
    // A CREATED series' initial colour + width comes from the resolved style;
    // existing series recolour in place via the restyle path.
    const overlayStyle = container ? resolveChartStyle(container, theme) : null

    const desired = new Map<string, OverlaySpec>()
    for (const spec of overlays ?? []) {
      // price_line / supertrend / ichimoku / oscillators / price-structure draw via
      // their own paths — skip the generic single-line path (and its warning).
      if (spec.kind === 'price_line') continue
      if (spec.kind === 'supertrend') continue
      if (spec.kind === 'ichimoku') continue
      if (isOscillatorOverlay(spec.kind)) continue
      if (isStructureOverlay(spec.kind)) continue
      if (!isSupportedOverlay(spec.kind)) {
        console.warn(
          `[CandlestickChart] unsupported overlay kind "${spec.kind}" — ignored (MVP renders ema/sma only)`,
        )
        continue
      }
      if (hidden.has(overlayLayerId(spec))) continue
      desired.set(overlayKey(spec), spec)
    }

    for (const [key, entry] of overlaySeries) {
      if (!desired.has(key)) {
        chart.removeSeries(entry.series)
        overlaySeries.delete(key)
      }
    }

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
  }

  private reconcileSupertrend(
    chart: IChartApi,
    container: HTMLDivElement | null,
    { bars, overlays, hidden, theme }: OverlayReconcileParams,
  ): void {
    const supertrendSeries = this.supertrendSeriesRef.current
    const overlayStyle = container ? resolveChartStyle(container, theme) : null
    const upColor = overlayStyle?.colors.markerBullish ?? DEFAULT_MARKER_COLORS.bullish
    const downColor = overlayStyle?.colors.markerBearish ?? DEFAULT_MARKER_COLORS.bearish

    const desiredSt = new Map<string, OverlaySpec>()
    for (const spec of overlays ?? []) {
      if (spec.kind !== 'supertrend') continue
      if (hidden.has(overlayLayerId(spec))) continue
      desiredSt.set(overlayKey(spec), spec)
    }
    for (const [key, entry] of supertrendSeries) {
      if (!desiredSt.has(key)) {
        chart.removeSeries(entry.up)
        chart.removeSeries(entry.down)
        supertrendSeries.delete(key)
      }
    }
    for (const [key, spec] of desiredSt) {
      let entry = supertrendSeries.get(key)
      if (entry === undefined) {
        const lineOpts = { lineWidth: 2 as const, priceLineVisible: false, lastValueVisible: false }
        const up = chart.addSeries(LineSeries, { color: upColor, ...lineOpts })
        const down = chart.addSeries(LineSeries, { color: downColor, ...lineOpts })
        entry = { up, down }
        supertrendSeries.set(key, entry)
      } else {
        entry.up.applyOptions({ color: upColor })
        entry.down.applyOptions({ color: downColor })
      }
      const points = computeSupertrend(bars, spec.period ?? 10, spec.multiplier ?? 3)
      const bands = supertrendBands(points)
      entry.up.setData(bands.up)
      entry.down.setData(bands.down)
    }
  }

  private reconcileBbands(
    chart: IChartApi,
    { bars, overlays, hidden }: OverlayReconcileParams,
  ): void {
    const bbandsSeries = this.bbandsSeriesRef.current
    const desired = new Map<string, OverlaySpec>()
    for (const spec of overlays ?? []) {
      if (spec.kind !== 'bbands') continue
      if (hidden.has(overlayLayerId(spec))) continue
      desired.set(overlayKey(spec), spec)
    }
    for (const [key, entry] of bbandsSeries) {
      if (!desired.has(key)) {
        chart.removeSeries(entry.upper)
        chart.removeSeries(entry.middle)
        chart.removeSeries(entry.lower)
        bbandsSeries.delete(key)
      }
    }
    for (const [key, spec] of desired) {
      let entry = bbandsSeries.get(key)
      if (entry === undefined) {
        const band = {
          color: BBANDS_LINE_COLOR,
          lineWidth: 1 as const,
          priceLineVisible: false,
          lastValueVisible: false,
        }
        const upper = chart.addSeries(LineSeries, band)
        const middle = chart.addSeries(LineSeries, { ...band, lineStyle: LineStyle.Dashed })
        const lower = chart.addSeries(LineSeries, band)
        entry = { upper, middle, lower }
        bbandsSeries.set(key, entry)
      }
      const data = computeBbands(
        bars,
        spec.period ?? BBANDS_DEFAULT_PERIOD,
        spec.multiplier ?? BBANDS_DEFAULT_K,
      )
      entry.upper.setData(data.upper)
      entry.middle.setData(data.middle)
      entry.lower.setData(data.lower)
    }
  }

  /** Reconcile horizontal `price_line` overlays on the main series. Recolours kept
   * lines in place, so the caller keys its effect on the theme + styleVersion. */
  reconcilePriceLines(
    series: MainSeries,
    container: HTMLDivElement | null,
    { overlays, hidden, theme }: PriceLineReconcileParams,
  ): void {
    if (container === null) return
    const priceLines = this.priceLinesRef.current
    const colors = chartColorsFrom(resolveChartStyle(container, theme))
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
  }

  /** Drop bookkeeping after `chart.remove()` disposed every series + price line. */
  clear(): void {
    this.overlaySeriesRef.current.clear()
    this.supertrendSeriesRef.current.clear()
    this.bbandsSeriesRef.current.clear()
    this.priceLinesRef.current.clear()
  }
}
