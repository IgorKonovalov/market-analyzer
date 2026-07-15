/**
 * ChartController — the imperative lightweight-charts core extracted from
 * CandlestickChart (Plan 0098 / ADR-0092). Owns the chart instance, the main +
 * always-on series (SeriesRegistry), the PaneRegistry, and the five main-series
 * primitives (PrimitiveHub), behind a declarative API the React component drives.
 * Plain TypeScript — no React import.
 *
 * Phase 1 (walking skeleton): `mount` / `setBars` / `dispose`, plus read handles
 * for the concerns still living in React hooks (overlays, oscillator panes,
 * primitive feeds, restyle, axis, forming-bar) that read the controller's refs
 * until later phases fold them in. The component instantiates the controller once
 * and reuses it across candle-type rebuilds (dispose → mount on the same instance);
 * the ref-object identities are stable across that rebuild, matching the old
 * component refs the hooks captured.
 */
import { ColorType, createChart } from 'lightweight-charts'
import type { IChartApi, ISeriesApi, Logical } from 'lightweight-charts'

import type { Bar } from '../../types/sidecar/bar'
import type { CandleSeriesType } from '../chartStyle'
import { resolveChartStyle } from '../chartStyle'
import {
  PRICE_SCALE_ID,
  PRICE_SCALE_MARGINS,
  VOLUME_SCALE_ID,
  VOLUME_SCALE_MARGINS,
  chartColorsFrom,
  type MainSeries,
} from '../chartSeries'
import { PaneRegistry } from '../panes'
import type { EffectiveTheme } from '../theme'
import type { DivergencePrimitive } from '../divergences'
import type { DrawingPrimitive } from '../drawings'
import type { IchimokuPrimitive } from '../ichimoku'
import type { PatternSpanPrimitive } from '../spans'
import type { TrendlinePrimitive } from '../trendlines'
import { PrimitiveHub } from './primitiveHub'
import type { MutRef } from './ref'
import { SeriesRegistry } from './seriesRegistry'

export interface MountOptions {
  candleType: CandleSeriesType
  theme: EffectiveTheme
}

export class ChartController {
  private readonly seriesRegistry = new SeriesRegistry()
  private readonly primitives = new PrimitiveHub()

  readonly chartRef: MutRef<IChartApi> = { current: null }
  readonly paneRegistryRef: MutRef<PaneRegistry> = { current: null }

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

  /** Dispose the chart (which disposes its series, panes and primitives) and drop
   * all bookkeeping. Symmetric with `mount` — a mount → dispose → mount round-trip
   * leaves no stranded primitive or leaked chart. */
  dispose(): void {
    this.chartRef.current?.remove()
    this.chartRef.current = null
    this.paneRegistryRef.current = null
    this.seriesRegistry.clear()
    this.primitives.clear()
    this.prevBars = null
    this.prevFirstTs = null
    this.barCountValue = 0
  }
}
