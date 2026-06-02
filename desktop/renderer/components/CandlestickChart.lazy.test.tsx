/**
 * Plan 0030 phase 2 done-when: the scroll-anchored prepend. When `bars` is
 * replaced with a superset prepended by N older bars, the chart shifts the
 * visible logical range right by N so the viewport stays on the same bars;
 * when bars grow only on the right, no anchor-shift happens (it fits as before).
 *
 * jsdom has no canvas, so `lightweight-charts` is mocked. The time scale is a
 * single persistent stub (unlike the overlays spec's throwaway `timeScale()`)
 * so `getVisibleLogicalRange` / `setVisibleLogicalRange` / `fitContent` calls
 * are observable across renders.
 */
import { render } from '@testing-library/react'

import { CandlestickChart } from './CandlestickChart'
import type { Bar } from '../types/sidecar/bar'

interface FakeTimeScale {
  fitContent: jest.Mock
  getVisibleLogicalRange: jest.Mock
  setVisibleLogicalRange: jest.Mock
  subscribeVisibleLogicalRangeChange: jest.Mock
  unsubscribeVisibleLogicalRangeChange: jest.Mock
}

let fakeTimeScale: FakeTimeScale
let fakeChart: Record<string, unknown>

jest.mock('lightweight-charts', () => ({
  ColorType: { Solid: 'solid' },
  createChart: jest.fn(() => fakeChart),
}))

function buildFakeChart(): Record<string, unknown> {
  fakeTimeScale = {
    fitContent: jest.fn(),
    // The visible range before a prepend — full data [0, 29].
    getVisibleLogicalRange: jest.fn(() => ({ from: 0, to: 29 })),
    setVisibleLogicalRange: jest.fn(),
    subscribeVisibleLogicalRangeChange: jest.fn(),
    unsubscribeVisibleLogicalRangeChange: jest.fn(),
  }
  return {
    addCandlestickSeries: jest.fn(() => ({ setData: jest.fn(), setMarkers: jest.fn() })),
    addLineSeries: jest.fn(() => ({ setData: jest.fn(), applyOptions: jest.fn() })),
    addHistogramSeries: jest.fn(() => ({ setData: jest.fn(), applyOptions: jest.fn() })),
    priceScale: jest.fn(() => ({ applyOptions: jest.fn() })),
    removeSeries: jest.fn(),
    remove: jest.fn(),
    applyOptions: jest.fn(),
    timeScale: () => fakeTimeScale,
    subscribeClick: jest.fn(),
    unsubscribeClick: jest.fn(),
  }
}

beforeEach(() => {
  fakeChart = buildFakeChart()
})

const DAY_MS = 24 * 60 * 60 * 1000
const START = new Date('2026-04-01T00:00:00.000Z')

function bar(eventTs: string, close: number): Bar {
  return {
    symbol: 'AAPL',
    timeframe: '1d',
    event_ts: eventTs,
    open: close,
    high: close,
    low: close,
    close,
    volume: 1000,
    source: 'test',
  }
}

function dailyBars(from: Date, count: number): Bar[] {
  return Array.from({ length: count }, (_, i) =>
    bar(new Date(from.getTime() + i * DAY_MS).toISOString(), 100 + i),
  )
}

const BASE = dailyBars(START, 30) // logical indices 0..29
const N = 5
const PREPENDED = [...dailyBars(new Date(START.getTime() - N * DAY_MS), N), ...BASE]
const APPENDED = [...BASE, ...dailyBars(new Date(START.getTime() + 30 * DAY_MS), N)]

it('shifts the visible range right by N when N older bars are prepended', () => {
  const { rerender } = render(<CandlestickChart bars={BASE} />)
  // Mount fits the initial data; no anchor-shift yet.
  expect(fakeTimeScale.fitContent).toHaveBeenCalledTimes(1)
  expect(fakeTimeScale.setVisibleLogicalRange).not.toHaveBeenCalled()

  rerender(<CandlestickChart bars={PREPENDED} />)

  // Viewport stays on the same bars: prior [0, 29] shifted right by N.
  expect(fakeTimeScale.setVisibleLogicalRange).toHaveBeenCalledTimes(1)
  expect(fakeTimeScale.setVisibleLogicalRange).toHaveBeenCalledWith({ from: N, to: 29 + N })
  // The prepend did NOT re-fit (that would discard the anchor).
  expect(fakeTimeScale.fitContent).toHaveBeenCalledTimes(1)
})

it('does not anchor-shift when bars grow only on the right', () => {
  const { rerender } = render(<CandlestickChart bars={BASE} />)
  expect(fakeTimeScale.fitContent).toHaveBeenCalledTimes(1)

  rerender(<CandlestickChart bars={APPENDED} />)

  expect(fakeTimeScale.setVisibleLogicalRange).not.toHaveBeenCalled()
  // Forward growth keeps the existing fit-on-update behavior.
  expect(fakeTimeScale.fitContent).toHaveBeenCalledTimes(2)
})
