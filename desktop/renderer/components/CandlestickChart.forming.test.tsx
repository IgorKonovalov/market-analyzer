/**
 * Plan 0049 phase 10 done-when: the live forming-bar update from the polled quote.
 *
 * Drives the REAL component with a mocked `lightweight-charts` whose candlestick
 * series records `update`/`setData`, so we can assert: a quote whose `as_of` falls
 * within the latest bar's period updates that bar via `series.update()` (close
 * tracks the quote, high/low extend) with NO extra `setData`/refetch; a quote that
 * predates the latest bar's period — or has crossed into a not-yet-fetched new
 * period — modifies no bar; a closed bar is never rewritten.
 */
import '@testing-library/jest-dom'
import { render } from '@testing-library/react'

import { CandlestickChart } from './CandlestickChart'
import type { Bar } from '../types/sidecar/bar'
import type { QuoteResponse } from '../types/sidecar/quote-response'

interface MockSeries {
  setData: jest.Mock
  update: jest.Mock
}
let mockSeries: MockSeries | undefined

jest.mock('lightweight-charts', () => {
  const series = {
    setData: jest.fn(),
    setMarkers: jest.fn(),
    applyOptions: jest.fn(),
    update: jest.fn(),
    attachPrimitive: jest.fn(),
    detachPrimitive: jest.fn(),
  }
  return {
    ColorType: { Solid: 'solid' },
    createChart: jest.fn(() => ({
      addCandlestickSeries: jest.fn(() => {
        mockSeries = series
        return series
      }),
      addLineSeries: jest.fn(() => ({ setData: jest.fn(), applyOptions: jest.fn() })),
      addHistogramSeries: jest.fn(() => ({ setData: jest.fn(), applyOptions: jest.fn() })),
      priceScale: jest.fn(() => ({ applyOptions: jest.fn() })),
      removeSeries: jest.fn(),
      remove: jest.fn(),
      applyOptions: jest.fn(),
      timeScale: () => ({
        fitContent: jest.fn(),
        getVisibleLogicalRange: jest.fn(() => null),
        setVisibleLogicalRange: jest.fn(),
        subscribeVisibleLogicalRangeChange: jest.fn(),
        unsubscribeVisibleLogicalRangeChange: jest.fn(),
      }),
      subscribeClick: jest.fn(),
      unsubscribeClick: jest.fn(),
      subscribeCrosshairMove: jest.fn(),
      unsubscribeCrosshairMove: jest.fn(),
    })),
  }
})

const LAST_BAR_DAY = Date.UTC(2026, 4, 20) // 2026-05-20T00:00:00Z
const BARS: Bar[] = [
  {
    symbol: 'BTC-USD',
    timeframe: '1d',
    event_ts: '2026-05-18T00:00:00.000Z',
    open: 90,
    high: 95,
    low: 88,
    close: 94,
    volume: 1000,
    source: 'fixture',
  },
  {
    symbol: 'BTC-USD',
    timeframe: '1d',
    event_ts: '2026-05-19T00:00:00.000Z',
    open: 94,
    high: 99,
    low: 93,
    close: 98,
    volume: 1000,
    source: 'fixture',
  },
  {
    symbol: 'BTC-USD',
    timeframe: '1d',
    event_ts: '2026-05-20T00:00:00.000Z',
    open: 100,
    high: 102,
    low: 99,
    close: 101,
    volume: 1000,
    source: 'fixture',
  },
]

function quote(asOf: string, price: number): QuoteResponse {
  return { symbol: 'BTC-USD', price, change_pct: null, currency: 'USD', as_of: asOf }
}

beforeEach(() => {
  // The mocked series is a module-level singleton, so its jest.fn call records
  // persist between tests — clear them so each case sees only its own calls.
  jest.clearAllMocks()
  mockSeries = undefined
})

it('updates the forming bar when the quote is within the latest bar period', () => {
  render(
    <CandlestickChart
      bars={BARS}
      symbol="BTC-USD"
      timeframe="1d"
      quote={quote('2026-05-20T14:30:00.000Z', 105)}
    />,
  )
  expect(mockSeries).toBeDefined()
  // close tracks the quote; high extends UP to 105; low stays (quote > low); open unchanged.
  expect(mockSeries!.update).toHaveBeenCalledWith({
    time: Math.floor(LAST_BAR_DAY / 1000),
    open: 100,
    high: 105,
    low: 99,
    close: 105,
  })
})

it('extends the low when the quote dips below the bar low', () => {
  render(
    <CandlestickChart
      bars={BARS}
      symbol="BTC-USD"
      timeframe="1d"
      quote={quote('2026-05-20T09:00:00.000Z', 97)}
    />,
  )
  expect(mockSeries!.update).toHaveBeenCalledWith({
    time: Math.floor(LAST_BAR_DAY / 1000),
    open: 100,
    high: 102,
    low: 97,
    close: 97,
  })
})

it('does not update any bar when the quote predates the latest bar period', () => {
  render(
    <CandlestickChart
      bars={BARS}
      symbol="BTC-USD"
      timeframe="1d"
      quote={quote('2026-05-19T23:59:59.000Z', 105)}
    />,
  )
  expect(mockSeries!.update).not.toHaveBeenCalled()
})

it('does not fabricate a bar when the quote crossed into a not-yet-fetched period', () => {
  render(
    <CandlestickChart
      bars={BARS}
      symbol="BTC-USD"
      timeframe="1d"
      quote={quote('2026-05-21T00:00:00.000Z', 105)}
    />,
  )
  expect(mockSeries!.update).not.toHaveBeenCalled()
})

it('updates via series.update only — no extra setData (no full redraw / refetch)', () => {
  const { rerender } = render(
    <CandlestickChart bars={BARS} symbol="BTC-USD" timeframe="1d" quote={null} />,
  )
  const setDataCallsAfterMount = mockSeries!.setData.mock.calls.length

  // A new quote arrives (same bars) — must NOT trigger another setData.
  rerender(
    <CandlestickChart
      bars={BARS}
      symbol="BTC-USD"
      timeframe="1d"
      quote={quote('2026-05-20T12:00:00.000Z', 105)}
    />,
  )
  expect(mockSeries!.setData.mock.calls.length).toBe(setDataCallsAfterMount)
  expect(mockSeries!.update).toHaveBeenCalledTimes(1)
})
