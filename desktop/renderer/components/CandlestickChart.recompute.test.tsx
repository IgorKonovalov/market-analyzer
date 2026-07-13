/**
 * Plan 0064 phase 5 done-when (component slice): trendline recompute triggers.
 *
 * Drives the REAL component with a mocked `lightweight-charts` (stubbed visible
 * range + a captured range-change handler) and a mocked `api.scanChartPatterns`,
 * plus fake timers for the debounce. Asserts: mount issues exactly one
 * scanChartPatterns carrying the current visible range; a burst of settled
 * pan/zoom coalesces to one call; the manual "Scan chart patterns" button fires
 * on click; a remount (reload) re-derives via this path; and NO call is made
 * when symbol/timeframe are absent.
 */
import '@testing-library/jest-dom'
import { act, fireEvent, render, screen } from '@testing-library/react'

import { CandlestickChart } from './CandlestickChart'
import { api } from '../api/client'
import type { Bar } from '../types/sidecar/bar'

jest.mock('../api/client', () => {
  const actual = jest.requireActual('../api/client')
  return { ...actual, api: { ...actual.api, scanChartPatterns: jest.fn() } }
})

const mockScanChartPatterns = api.scanChartPatterns as jest.Mock

const VISIBLE_FROM = Math.floor(Date.UTC(2026, 3, 12) / 1000)
const VISIBLE_TO = Math.floor(Date.UTC(2026, 3, 18) / 1000)

let rangeHandler: (() => void) | null = null

jest.mock('lightweight-charts', () => {
  const { seriesDefs, createSeriesMarkers, dispatchAddSeries } = jest.requireActual(
    '../tests/chartMockShared',
  )
  const series = {
    setData: jest.fn(),
    setMarkers: jest.fn(),
    applyOptions: jest.fn(),
    attachPrimitive: jest.fn(),
    detachPrimitive: jest.fn(),
  }
  return {
    ...seriesDefs,
    createSeriesMarkers,
    ColorType: { Solid: 'solid' },
    createChart: jest.fn(() => ({
      ...jest.requireActual('../tests/chartMockShared').paneStubs,
      addSeries: dispatchAddSeries({
        candle: () => series,
        line: () => ({ setData: jest.fn(), applyOptions: jest.fn() }),
      }),
      priceScale: jest.fn(() => ({ applyOptions: jest.fn() })),
      removeSeries: jest.fn(),
      remove: jest.fn(),
      applyOptions: jest.fn(),
      timeScale: () => ({
        fitContent: jest.fn(),
        getVisibleLogicalRange: jest.fn(() => null),
        setVisibleLogicalRange: jest.fn(),
        subscribeVisibleLogicalRangeChange: (h: () => void) => {
          rangeHandler = h
        },
        unsubscribeVisibleLogicalRangeChange: () => {
          rangeHandler = null
        },
        getVisibleRange: jest.fn(() => ({ from: VISIBLE_FROM, to: VISIBLE_TO })),
      }),
      subscribeClick: jest.fn(),
      unsubscribeClick: jest.fn(),
      subscribeCrosshairMove: jest.fn(),
      unsubscribeCrosshairMove: jest.fn(),
    })),
  }
})

const BARS: Bar[] = Array.from({ length: 30 }, (_, i) => ({
  symbol: 'BTC-USD',
  timeframe: '1d',
  event_ts: new Date(Date.UTC(2026, 3, 1 + i)).toISOString(),
  open: 100,
  high: 102,
  low: 99,
  close: 101,
  volume: 1_000_000,
  source: 'fixture',
}))

const EXPECTED_REQUEST = {
  symbol: 'BTC-USD',
  timeframe: '1d',
  range_start: new Date(VISIBLE_FROM * 1000).toISOString(),
  range_end: new Date(VISIBLE_TO * 1000).toISOString(),
}

beforeEach(() => {
  jest.useFakeTimers()
  rangeHandler = null
  mockScanChartPatterns.mockReset()
  mockScanChartPatterns.mockResolvedValue({ published: true, count: 2 })
})

afterEach(() => {
  jest.useRealTimers()
})

function advance(ms: number): void {
  act(() => {
    jest.advanceTimersByTime(ms)
  })
}

it('mount issues exactly one scanChartPatterns carrying the current visible range', () => {
  render(<CandlestickChart bars={BARS} symbol="BTC-USD" timeframe="1d" />)
  expect(mockScanChartPatterns).not.toHaveBeenCalled() // debounced, not yet

  advance(500)
  expect(mockScanChartPatterns).toHaveBeenCalledTimes(1)
  expect(mockScanChartPatterns).toHaveBeenCalledWith(EXPECTED_REQUEST)
})

it('coalesces a burst of settled range changes into a single recompute', () => {
  render(<CandlestickChart bars={BARS} symbol="BTC-USD" timeframe="1d" />)

  // Rapid pan/zoom within the debounce window keeps resetting the timer.
  act(() => {
    rangeHandler?.()
    jest.advanceTimersByTime(100)
    rangeHandler?.()
    jest.advanceTimersByTime(100)
    rangeHandler?.()
  })
  expect(mockScanChartPatterns).not.toHaveBeenCalled()

  advance(500)
  expect(mockScanChartPatterns).toHaveBeenCalledTimes(1)
})

it('the manual "Chart patterns" button fires a scan on click', async () => {
  render(<CandlestickChart bars={BARS} symbol="BTC-USD" timeframe="1d" />)

  // Click before the mount debounce fires, so this is the only call. Await the
  // async click handler so its post-await status update (setChartScanStatus →
  // 'done') settles inside act — the resolved scan promise is a microtask, which
  // fake timers don't gate, so this doesn't need the debounce advanced.
  await act(async () => {
    fireEvent.click(screen.getByTestId('scan-chart-patterns-button'))
  })
  expect(mockScanChartPatterns).toHaveBeenCalledTimes(1)
  expect(mockScanChartPatterns).toHaveBeenCalledWith(EXPECTED_REQUEST)
})

it('re-derives on a remount (reload → trendlines return via this path)', () => {
  const { unmount } = render(<CandlestickChart bars={BARS} symbol="BTC-USD" timeframe="1d" />)
  advance(500)
  expect(mockScanChartPatterns).toHaveBeenCalledTimes(1)
  unmount()

  mockScanChartPatterns.mockClear()
  render(<CandlestickChart bars={BARS} symbol="BTC-USD" timeframe="1d" />)
  advance(500)
  expect(mockScanChartPatterns).toHaveBeenCalledTimes(1)
})

it('makes NO call when symbol/timeframe are absent', () => {
  render(<CandlestickChart bars={BARS} />)
  advance(1000)
  expect(mockScanChartPatterns).not.toHaveBeenCalled()
})
