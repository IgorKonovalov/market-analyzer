import { renderHook } from '@testing-library/react'
import type { RefObject } from 'react'
import type { IChartApi, ISeriesApi } from 'lightweight-charts'

import { useOverlaySeries } from './useOverlaySeries'
import type { OverlayEntry } from '../lib/chartSeries'
import type { EffectiveTheme } from '../lib/theme'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'

function fakeChart() {
  const added: Array<ISeriesApi<'Line'> & { setData: jest.Mock; applyOptions: jest.Mock }> = []
  const removed: unknown[] = []
  const chart = {
    addLineSeries: () => {
      const s = { setData: jest.fn(), applyOptions: jest.fn() } as unknown as ISeriesApi<'Line'> & {
        setData: jest.Mock
        applyOptions: jest.Mock
      }
      added.push(s)
      return s
    },
    removeSeries: (s: unknown) => removed.push(s),
  } as unknown as IChartApi
  return { chart, added, removed }
}

const BARS: Bar[] = Array.from({ length: 60 }, (_, i) => ({
  event_ts: `2026-01-${String((i % 28) + 1).padStart(2, '0')}T00:00:00+00:00`,
  open: 100 + i,
  high: 101 + i,
  low: 99 + i,
  close: 100 + i,
  volume: 1000,
})) as Bar[]

const EMA: OverlaySpec = { kind: 'ema', period: 20 } as OverlaySpec
const themeRef: RefObject<EffectiveTheme> = { current: 'light' }

function run(
  chart: IChartApi,
  ref: RefObject<Map<string, OverlayEntry>>,
  overlays: OverlaySpec[],
  hidden: Set<string>,
  sync = jest.fn(),
) {
  return renderHook(() =>
    useOverlaySeries({ current: chart }, { current: document.createElement('div') }, ref, {
      bars: BARS,
      overlays,
      hidden,
      effectiveThemeRef: themeRef,
      rebuildToken: 'candles',
      syncTestRenderHook: sync,
    }),
  )
}

describe('useOverlaySeries', () => {
  it('adds a line series for an ema overlay, sets its data, and syncs the test hook', () => {
    const { chart, added } = fakeChart()
    const ref: RefObject<Map<string, OverlayEntry>> = { current: new Map() }
    const sync = jest.fn()
    run(chart, ref, [EMA], new Set(), sync)
    expect(added).toHaveLength(1)
    expect(added[0].setData).toHaveBeenCalledTimes(1)
    expect(ref.current?.size).toBe(1)
    expect(sync).toHaveBeenCalled()
  })

  it('removes the overlay series when its legend row is toggled off', () => {
    const { chart, removed } = fakeChart()
    const ref: RefObject<Map<string, OverlayEntry>> = { current: new Map() }
    const { rerender } = renderHook(
      (hidden: Set<string>) =>
        useOverlaySeries({ current: chart }, { current: document.createElement('div') }, ref, {
          bars: BARS,
          overlays: [EMA],
          hidden,
          effectiveThemeRef: themeRef,
          rebuildToken: 'candles',
          syncTestRenderHook: jest.fn(),
        }),
      { initialProps: new Set<string>() },
    )
    expect(ref.current?.size).toBe(1)
    rerender(new Set(['overlay:ema:20']))
    expect(removed).toHaveLength(1)
    expect(ref.current?.size).toBe(0)
  })

  it('warns and draws nothing for an unsupported overlay kind', () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {})
    const { chart, added } = fakeChart()
    const ref: RefObject<Map<string, OverlayEntry>> = { current: new Map() }
    run(chart, ref, [{ kind: 'rsi', period: 14 } as OverlaySpec], new Set())
    expect(added).toHaveLength(0)
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })
})
