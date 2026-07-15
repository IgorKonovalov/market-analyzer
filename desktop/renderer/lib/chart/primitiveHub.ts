/**
 * PrimitiveHub — the five ISeriesPrimitives that ride the MAIN price series for the
 * chart's whole life: the pattern-span band, the trendline overlay, the Ichimoku
 * cloud, the price-pane divergence, and freeform drawings. Attached once at chart
 * creation so they ride the live series and are disposed by `chart.remove()` — the
 * Plan 0064 fix that kept a hook-attached primitive from stranding on a discarded
 * StrictMode chart.
 *
 * Extracted from CandlestickChart's creation effect (Plan 0098 phase 1, ADR-0092).
 * Scaffold: this phase owns creation + attach + lifecycle; phase 3 folds the
 * per-primitive feed hooks (useTrendlines / useIchimokuSeries / useDivergences /
 * useChartMarkers) into feed methods here.
 */
import { DivergencePrimitive, readDivergenceColors } from '../divergences'
import { DrawingPrimitive } from '../drawings'
import { IchimokuPrimitive, readIchimokuColors } from '../ichimoku'
import { PatternSpanPrimitive } from '../spans'
import { TrendlinePrimitive, readTrendlineColors } from '../trendlines'
import type { ChartColors, MainSeries } from '../chartSeries'
import type { MutRef } from './ref'

export class PrimitiveHub {
  readonly spanRef: MutRef<PatternSpanPrimitive> = { current: null }
  readonly trendlineRef: MutRef<TrendlinePrimitive> = { current: null }
  readonly ichimokuRef: MutRef<IchimokuPrimitive> = { current: null }
  readonly divergencePriceRef: MutRef<DivergencePrimitive> = { current: null }
  readonly drawingRef: MutRef<DrawingPrimitive> = { current: null }

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

  /** Drop bookkeeping after `chart.remove()` detached the primitives. */
  clear(): void {
    this.spanRef.current = null
    this.trendlineRef.current = null
    this.ichimokuRef.current = null
    this.divergencePriceRef.current = null
    this.drawingRef.current = null
  }
}
