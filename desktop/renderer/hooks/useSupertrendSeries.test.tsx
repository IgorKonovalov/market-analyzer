import { renderHook } from '@testing-library/react'
import type { RefObject } from 'react'
import type { IChartApi, ISeriesApi } from 'lightweight-charts'

import { useSupertrendSeries } from './useSupertrendSeries'
import type { EffectiveTheme } from '../lib/theme'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'

type Pair = { up: ISeriesApi<'Line'>; down: ISeriesApi<'Line'> }

function fakeChart() {
  const added: Array<{ setData: jest.Mock; applyOptions: jest.Mock }> = []
  const removed: unknown[] = []
  const chart = {
    addSeries: () => {
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

const ST: OverlaySpec = { kind: 'supertrend', period: 10, multiplier: 3 } as OverlaySpec
const themeRef: RefObject<EffectiveTheme> = { current: 'light' }

describe('useSupertrendSeries', () => {
  it('draws TWO masked line series (up/down) for a supertrend overlay and sets both', () => {
    const { chart, added } = fakeChart()
    const ref: RefObject<Map<string, Pair>> = { current: new Map() }
    renderHook(() =>
      useSupertrendSeries({ current: chart }, { current: document.createElement('div') }, ref, {
        bars: BARS,
        overlays: [ST],
        hidden: new Set(),
        effectiveThemeRef: themeRef,
        rebuildToken: 'candles',
      }),
    )
    expect(added).toHaveLength(2) // up + down
    expect(added[0].setData).toHaveBeenCalledTimes(1)
    expect(added[1].setData).toHaveBeenCalledTimes(1)
    expect(ref.current?.size).toBe(1)
  })

  it('removes both series when the supertrend row is toggled off', () => {
    const { chart, removed } = fakeChart()
    const ref: RefObject<Map<string, Pair>> = { current: new Map() }
    const { rerender } = renderHook(
      (hidden: Set<string>) =>
        useSupertrendSeries({ current: chart }, { current: document.createElement('div') }, ref, {
          bars: BARS,
          overlays: [ST],
          hidden,
          effectiveThemeRef: themeRef,
          rebuildToken: 'candles',
        }),
      { initialProps: new Set<string>() },
    )
    expect(ref.current?.size).toBe(1)
    rerender(new Set(['overlay:supertrend:10']))
    expect(removed).toHaveLength(2) // up + down removed
    expect(ref.current?.size).toBe(0)
  })
})
