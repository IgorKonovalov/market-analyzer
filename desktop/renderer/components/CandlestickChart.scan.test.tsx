/**
 * Plan 0049 phase 8 done-when: the "Candlesticks" button (renamed from "Scan
 * patterns" in Plan 0071 phase 1; the `scan-patterns-button` testid is stable).
 *
 * Drives the REAL component with a mocked `lightweight-charts` (a stubbed visible
 * range) and a mocked `api.scanPatterns`, so we can assert: clicking reads the
 * chart's CURRENT visible range (not the full buffer) + the active symbol/timeframe
 * and issues exactly one POST through the typed client; a {published,count} ack
 * shows a transient "N patterns" state; count 0 shows "no patterns in view"; an
 * error shows a non-crashing message. The markers themselves arrive via SSE (the
 * phase 6/7 path) — not asserted here.
 */
import '@testing-library/jest-dom'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

import { CandlestickChart } from './CandlestickChart'
import { api } from '../api/client'
import type { Bar } from '../types/sidecar/bar'

jest.mock('../api/client', () => {
  const actual = jest.requireActual('../api/client')
  return { ...actual, api: { ...actual.api, scanPatterns: jest.fn() } }
})

const mockScanPatterns = api.scanPatterns as jest.Mock

// The stubbed visible range the chart reports (epoch seconds), deliberately
// NARROWER than the bar buffer so the test proves the request uses the VISIBLE
// range, not the full data.
const VISIBLE_FROM = Math.floor(Date.UTC(2026, 3, 12) / 1000)
const VISIBLE_TO = Math.floor(Date.UTC(2026, 3, 18) / 1000)

jest.mock('lightweight-charts', () => {
  const series = {
    setData: jest.fn(),
    setMarkers: jest.fn(),
    applyOptions: jest.fn(),
    attachPrimitive: jest.fn(),
    detachPrimitive: jest.fn(),
  }
  return {
    ColorType: { Solid: 'solid' },
    createChart: jest.fn(() => ({
      addCandlestickSeries: jest.fn(() => series),
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
        getVisibleRange: jest.fn(() => ({ from: VISIBLE_FROM, to: VISIBLE_TO })),
      }),
      subscribeClick: jest.fn(),
      unsubscribeClick: jest.fn(),
      subscribeCrosshairMove: jest.fn(),
      unsubscribeCrosshairMove: jest.fn(),
    })),
  }
})

// A wider buffer than the visible range, to prove the request uses the latter.
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

beforeEach(() => {
  mockScanPatterns.mockReset()
})

function renderChart() {
  return render(<CandlestickChart bars={BARS} symbol="BTC-USD" timeframe="1d" />)
}

it('posts the VISIBLE range + active symbol/timeframe through the typed client', async () => {
  mockScanPatterns.mockResolvedValue({ published: true, count: 3 })
  renderChart()

  fireEvent.click(screen.getByTestId('scan-patterns-button'))

  await waitFor(() => expect(mockScanPatterns).toHaveBeenCalledTimes(1))
  expect(mockScanPatterns).toHaveBeenCalledWith({
    symbol: 'BTC-USD',
    timeframe: '1d',
    range_start: new Date(VISIBLE_FROM * 1000).toISOString(),
    range_end: new Date(VISIBLE_TO * 1000).toISOString(),
  })
})

it('shows a transient "N patterns" state on a non-empty ack', async () => {
  mockScanPatterns.mockResolvedValue({ published: true, count: 3 })
  renderChart()

  fireEvent.click(screen.getByTestId('scan-patterns-button'))

  await waitFor(() =>
    expect(screen.getByTestId('scan-patterns-status')).toHaveTextContent('3 patterns'),
  )
})

it('shows "no patterns in view" when the sweep finds nothing', async () => {
  mockScanPatterns.mockResolvedValue({ published: false, count: 0 })
  renderChart()

  fireEvent.click(screen.getByTestId('scan-patterns-button'))

  await waitFor(() =>
    expect(screen.getByTestId('scan-patterns-status')).toHaveTextContent('No patterns in view'),
  )
})

it('shows a non-crashing error message when the scan fails', async () => {
  mockScanPatterns.mockRejectedValue(new Error('boom'))
  renderChart()

  fireEvent.click(screen.getByTestId('scan-patterns-button'))

  await waitFor(() => expect(screen.getByTestId('scan-patterns-error')).toBeInTheDocument())
})

it('disables the button while a scan is in flight', async () => {
  let resolve!: (v: { published: boolean; count: number }) => void
  mockScanPatterns.mockReturnValue(
    new Promise((r) => {
      resolve = r
    }),
  )
  renderChart()

  const button = screen.getByTestId('scan-patterns-button')
  fireEvent.click(button)
  await waitFor(() => expect(button).toBeDisabled())
  expect(button).toHaveTextContent('Scanning…')

  resolve({ published: true, count: 1 })
  await waitFor(() => expect(button).not.toBeDisabled())
})
