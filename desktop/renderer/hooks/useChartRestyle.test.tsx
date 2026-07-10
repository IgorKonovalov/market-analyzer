import { renderHook } from '@testing-library/react'
import type { IChartApi, ISeriesApi } from 'lightweight-charts'

import { useChartRestyle, type ChartRestyleRefs } from './useChartRestyle'
import type { MainSeries, OverlayEntry } from '../lib/chartSeries'
import type { OverlaySpec } from '../types/events'

function lineSeries(): { series: ISeriesApi<'Line'>; applyOptions: jest.Mock } {
  const applyOptions = jest.fn()
  return { series: { applyOptions } as unknown as ISeriesApi<'Line'>, applyOptions }
}

function harness() {
  const chartApply = jest.fn()
  const mainApply = jest.fn()
  const chart = { applyOptions: chartApply } as unknown as IChartApi
  const main = { applyOptions: mainApply } as unknown as MainSeries
  const vol = lineSeries()
  const overlay = lineSeries()
  const overlaySeriesRef = {
    current: new Map<string, OverlayEntry>([
      ['ema:20', { spec: { kind: 'ema', period: 20 } as OverlaySpec, series: overlay.series }],
    ]),
  }
  const refs: ChartRestyleRefs = {
    containerRef: { current: document.createElement('div') },
    chartRef: { current: chart },
    seriesRef: { current: main },
    volumeSeriesRef: { current: vol.series as unknown as ISeriesApi<'Histogram'> },
    volumeMaSeriesRef: { current: lineSeries().series },
    vwapSeriesRef: { current: lineSeries().series },
    obvSeriesRef: { current: lineSeries().series },
    overlaySeriesRef,
    supertrendSeriesRef: { current: new Map() },
  }
  return { refs, chartApply, mainApply, overlayApply: overlay.applyOptions }
}

describe('useChartRestyle', () => {
  it('re-applies chart chrome, the main series colours, and each overlay series in place', () => {
    const h = harness()
    renderHook(() =>
      useChartRestyle(h.refs, { effectiveTheme: 'dark', styleVersion: 0, candleType: 'candles' }),
    )
    // Chart chrome (layout text + grid) re-applied.
    expect(h.chartApply).toHaveBeenCalledTimes(1)
    expect(h.chartApply.mock.calls[0][0]).toHaveProperty('layout')
    // Candle up/down colours re-applied to the main series.
    expect(h.mainApply).toHaveBeenCalledTimes(1)
    expect(h.mainApply.mock.calls[0][0]).toHaveProperty('upColor')
    // The kept overlay series recolours in place (colour + width).
    expect(h.overlayApply).toHaveBeenCalledTimes(1)
    expect(h.overlayApply.mock.calls[0][0]).toHaveProperty('color')
    expect(h.overlayApply.mock.calls[0][0]).toHaveProperty('lineWidth')
  })

  it('no-ops safely when the chart is not yet created', () => {
    const h = harness()
    h.refs.chartRef = { current: null }
    renderHook(() =>
      useChartRestyle(h.refs, { effectiveTheme: 'light', styleVersion: 0, candleType: 'candles' }),
    )
    expect(h.chartApply).not.toHaveBeenCalled()
  })
})
