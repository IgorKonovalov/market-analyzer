import { renderHook } from '@testing-library/react'
import type { RefObject } from 'react'
import type { IChartApi, ISeriesApi } from 'lightweight-charts'

import { useBbandsSeries } from './useBbandsSeries'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'

type Triple = {
  upper: ISeriesApi<'Line'>
  middle: ISeriesApi<'Line'>
  lower: ISeriesApi<'Line'>
}

function fakeChart() {
  const added: Array<{ setData: jest.Mock; applyOptions: jest.Mock }> = []
  const removed: unknown[] = []
  const chart = {
    addLineSeries: () => {
      const s = { setData: jest.fn(), applyOptions: jest.fn() }
      added.push(s)
      return s as unknown as ISeriesApi<'Line'>
    },
    removeSeries: (s: unknown) => removed.push(s),
  } as unknown as IChartApi
  return { chart, added, removed }
}

const BARS: Bar[] = Array.from({ length: 40 }, (_, i) => ({
  event_ts: `2026-02-${String((i % 28) + 1).padStart(2, '0')}T00:00:00+00:00`,
  open: 100 + i,
  high: 102 + i,
  low: 98 + i,
  close: 100 + i,
  volume: 1000,
})) as Bar[]

const BB: OverlaySpec = { kind: 'bbands', period: 20, multiplier: 2 } as OverlaySpec

describe('useBbandsSeries', () => {
  it('draws THREE line series (upper/middle/lower) for a bbands overlay and sets all three', () => {
    const { chart, added } = fakeChart()
    const ref: RefObject<Map<string, Triple>> = { current: new Map() }
    renderHook(() =>
      useBbandsSeries({ current: chart }, ref, {
        bars: BARS,
        overlays: [BB],
        hidden: new Set(),
        rebuildToken: 'candles',
      }),
    )
    expect(added).toHaveLength(3) // upper + middle + lower
    added.forEach((s) => expect(s.setData).toHaveBeenCalledTimes(1))
    expect(ref.current?.size).toBe(1)
  })

  it('removes all three series when the bbands row is toggled off', () => {
    const { chart, removed } = fakeChart()
    const ref: RefObject<Map<string, Triple>> = { current: new Map() }
    const { rerender } = renderHook(
      (hidden: Set<string>) =>
        useBbandsSeries({ current: chart }, ref, {
          bars: BARS,
          overlays: [BB],
          hidden,
          rebuildToken: 'candles',
        }),
      { initialProps: new Set<string>() },
    )
    expect(ref.current?.size).toBe(1)
    rerender(new Set(['overlay:bbands:20']))
    expect(removed).toHaveLength(3) // upper + middle + lower removed
    expect(ref.current?.size).toBe(0)
  })

  it('draws nothing for a non-bbands overlay set', () => {
    const { chart, added } = fakeChart()
    const ref: RefObject<Map<string, Triple>> = { current: new Map() }
    renderHook(() =>
      useBbandsSeries({ current: chart }, ref, {
        bars: BARS,
        overlays: [{ kind: 'ema', period: 20 } as OverlaySpec],
        hidden: new Set(),
        rebuildToken: 'candles',
      }),
    )
    expect(added).toHaveLength(0)
    expect(ref.current?.size).toBe(0)
  })
})
