/**
 * SeriesRegistry — the main price series plus the three always-on price/volume
 * series (volume histogram, volume MA, VWAP). Extracted from CandlestickChart's
 * chart-creation + bars effects (Plan 0098 phase 1, ADR-0092). Pure imperative
 * wiring over lightweight-charts; no React.
 *
 * The OBV series and the oscillator sub-panes are lazily reconciled elsewhere
 * (useObvPane / useOscillatorPanes) and are NOT owned here — they come and go with
 * the layers legend, unlike these four which live for the chart's whole life.
 */
import { HistogramSeries, LineSeries } from 'lightweight-charts'
import type { IChartApi, ISeriesApi, LineWidth } from 'lightweight-charts'

import type { Bar } from '../../types/sidecar/bar'
import type { CandleSeriesType, ResolvedChartStyle } from '../chartStyle'
import {
  PRICE_SCALE_ID,
  VOLUME_SCALE_ID,
  applyMainColors,
  createMainSeries,
  setMainData,
  type ChartColors,
  type MainSeries,
} from '../chartSeries'
import {
  VOLUME_MA_PERIOD,
  VWAP_PERIOD,
  computeVolumeBars,
  computeVolumeMa,
  computeVwap,
} from '../volume'
import type { MutRef } from './ref'

export class SeriesRegistry {
  readonly mainRef: MutRef<MainSeries> = { current: null }
  readonly volumeRef: MutRef<ISeriesApi<'Histogram'>> = { current: null }
  readonly volumeMaRef: MutRef<ISeriesApi<'Line'>> = { current: null }
  readonly vwapRef: MutRef<ISeriesApi<'Line'>> = { current: null }

  /** Create the main series (its concrete type chosen from `candleType`) plus the
   * three always-on series on a freshly-created chart. `chart.remove()` disposes
   * them; `clear()` drops our bookkeeping afterwards. */
  create(
    chart: IChartApi,
    candleType: CandleSeriesType,
    style: ResolvedChartStyle,
    colors: ChartColors,
  ): void {
    const series = createMainSeries(chart, candleType)
    applyMainColors(series, candleType, colors)

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceScaleId: VOLUME_SCALE_ID,
      color: colors.volume,
      priceFormat: { type: 'volume' },
      priceLineVisible: false,
      lastValueVisible: false,
    })
    const volumeMaSeries = chart.addSeries(LineSeries, {
      priceScaleId: VOLUME_SCALE_ID,
      color: colors.volumeMa,
      lineWidth: style.widths.volumeMa as LineWidth,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    const vwapSeries = chart.addSeries(LineSeries, {
      priceScaleId: PRICE_SCALE_ID, // rides the main price scale alongside candles
      color: colors.vwap,
      lineWidth: style.widths.vwap as LineWidth,
      priceLineVisible: false,
      lastValueVisible: false,
    })

    this.mainRef.current = series
    this.volumeRef.current = volumeSeries
    this.volumeMaRef.current = volumeMaSeries
    this.vwapRef.current = vwapSeries
  }

  /** Push bar-derived data into the main + always-on series. Empty `bars` yields
   * empty arrays (no NaN/Infinity reaches lightweight-charts). */
  setData(bars: Bar[], candleType: CandleSeriesType): void {
    const series = this.mainRef.current
    if (series === null) return
    setMainData(series, candleType, bars)
    this.volumeRef.current?.setData(computeVolumeBars(bars))
    this.volumeMaRef.current?.setData(computeVolumeMa(bars, VOLUME_MA_PERIOD))
    this.vwapRef.current?.setData(computeVwap(bars, VWAP_PERIOD))
  }

  /** Drop bookkeeping after `chart.remove()` disposed the series. */
  clear(): void {
    this.mainRef.current = null
    this.volumeRef.current = null
    this.volumeMaRef.current = null
    this.vwapRef.current = null
  }
}
