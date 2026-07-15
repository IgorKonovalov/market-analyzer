/**
 * applyRestyle headless spec (Plan 0098 phase 3, ADR-0092) — migrated from the
 * deleted useChartRestyle hook spec. Asserts the in-place recolour/rewidth applies
 * to the chart + every series without a remount.
 */
import type { IChartApi, ISeriesApi } from 'lightweight-charts'

import { applyRestyle } from './restyle'
import type { MainSeries, OverlayEntry } from '../chartSeries'

function fakeSeries() {
  return { applyOptions: jest.fn() }
}

function baseParams() {
  const chart = { applyOptions: jest.fn() } as unknown as IChartApi
  const main = fakeSeries()
  const vol = fakeSeries()
  const volMa = fakeSeries()
  const vwap = fakeSeries()
  return {
    chart,
    mainSeries: main as unknown as MainSeries,
    container: document.createElement('div'),
    candleType: 'candles' as const,
    theme: 'light' as const,
    volumeSeries: vol as unknown as ISeriesApi<'Histogram'>,
    volumeMaSeries: volMa as unknown as ISeriesApi<'Line'>,
    vwapSeries: vwap as unknown as ISeriesApi<'Line'>,
    obvSeries: null,
    overlaySeries: new Map<string, OverlayEntry>(),
    supertrendSeries: new Map(),
    _refs: { chart, main, vol, volMa, vwap },
  }
}

describe('applyRestyle', () => {
  it('recolours the chart and every always-on series in place', () => {
    const p = baseParams()
    applyRestyle(p)
    expect((p.chart as unknown as { applyOptions: jest.Mock }).applyOptions).toHaveBeenCalled()
    expect(p._refs.main.applyOptions).toHaveBeenCalled()
    expect(p._refs.vol.applyOptions).toHaveBeenCalledWith(
      expect.objectContaining({ color: expect.any(String) }),
    )
    expect(p._refs.vwap.applyOptions).toHaveBeenCalledWith(
      expect.objectContaining({ color: expect.any(String), lineWidth: expect.any(Number) }),
    )
  })

  it('no-ops when unmounted (null chart / series / container)', () => {
    const chart = { applyOptions: jest.fn() } as unknown as IChartApi
    applyRestyle({
      chart,
      mainSeries: null,
      container: null,
      candleType: 'candles',
      theme: 'light',
      volumeSeries: null,
      volumeMaSeries: null,
      vwapSeries: null,
      obvSeries: null,
      overlaySeries: new Map(),
      supertrendSeries: new Map(),
    })
    expect((chart as unknown as { applyOptions: jest.Mock }).applyOptions).not.toHaveBeenCalled()
  })
})
