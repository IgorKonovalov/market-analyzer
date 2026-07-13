import { renderHook } from '@testing-library/react'
import type { RefObject } from 'react'
import type { IChartApi, ISeriesApi } from 'lightweight-charts'

import { useAnchoredVwapSeries } from './useAnchoredVwapSeries'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'

function fakeChart(): {
  chart: IChartApi
  added: Array<ISeriesApi<'Line'>>
  removed: Array<ISeriesApi<'Line'>>
  lastData: Map<ISeriesApi<'Line'>, unknown[]>
} {
  const added: Array<ISeriesApi<'Line'>> = []
  const removed: Array<ISeriesApi<'Line'>> = []
  const lastData = new Map<ISeriesApi<'Line'>, unknown[]>()
  const chart = {
    addSeries: () => {
      const series = {
        setData: (data: unknown[]) => lastData.set(series, data),
      } as unknown as ISeriesApi<'Line'>
      added.push(series)
      return series
    },
    removeSeries: (series: ISeriesApi<'Line'>) => removed.push(series),
  } as unknown as IChartApi
  return { chart, added, removed, lastData }
}

function iso(i: number): string {
  return `2025-01-${String(i + 1).padStart(2, '0')}T00:00:00+00:00`
}

function flatBars(): Bar[] {
  return [10, 20, 30, 40].map((tp, i) => ({
    symbol: 'T',
    timeframe: '1d',
    event_ts: iso(i),
    open: tp,
    high: tp,
    low: tp,
    close: tp,
    volume: 1000,
    source: 'test',
  }))
}

const AVWAP: OverlaySpec = { kind: 'anchored_vwap', anchor_ts: iso(1) }

describe('useAnchoredVwapSeries', () => {
  it('adds one line series for an anchored_vwap overlay and feeds it the accumulation', () => {
    const { chart, added, lastData } = fakeChart()
    const ref: RefObject<Map<string, ISeriesApi<'Line'>>> = { current: new Map() }
    renderHook(() =>
      useAnchoredVwapSeries({ current: chart }, ref, {
        bars: flatBars(),
        overlays: [AVWAP, { kind: 'ema', period: 20 }], // ema ignored
        hidden: new Set(),
        rebuildToken: 'candles',
      }),
    )
    expect(added).toHaveLength(1)
    expect(lastData.get(added[0])).toHaveLength(3) // bars 1..3 from the anchor
  })

  it('removes the series when toggled off in the legend', () => {
    const { chart, added, removed } = fakeChart()
    const ref: RefObject<Map<string, ISeriesApi<'Line'>>> = { current: new Map() }
    const props = {
      bars: flatBars(),
      overlays: [AVWAP] as OverlaySpec[],
      hidden: new Set<string>(),
      rebuildToken: 'candles',
    }
    const { rerender } = renderHook(
      (p: typeof props) => useAnchoredVwapSeries({ current: chart }, ref, p),
      { initialProps: props },
    )
    expect(added).toHaveLength(1)

    rerender({ ...props, hidden: new Set(['overlay:anchored_vwap:na']) })
    expect(removed).toHaveLength(1)
    expect(ref.current?.size).toBe(0)
  })
})
