/**
 * Plan 0049 phase 11 done-when: toggling an overlay must NOT reset the chart view.
 *
 * Drives the REAL component with a mocked `lightweight-charts` exposing a STABLE
 * `fitContent` spy, so we can assert: `fitContent` fires once on initial load and
 * again on a genuine data change (a new `bars` array), but NOT when a legend
 * checkbox toggles an overlay — while the overlay series is still removed/re-added
 * across the toggle.
 */
import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'

import { CandlestickChart } from './CandlestickChart'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'

interface MockLine {
  setData: jest.Mock
  applyOptions: jest.Mock
  _opts: { priceScaleId?: string }
}
let mockFitContent: jest.Mock | undefined
let mockCreatedLines: MockLine[] = []
let mockRemoved: MockLine[] = []

jest.mock('lightweight-charts', () => {
  const fitContent = jest.fn()
  const timeScale = {
    fitContent,
    getVisibleLogicalRange: jest.fn(() => ({ from: 5, to: 25 })),
    setVisibleLogicalRange: jest.fn(),
    subscribeVisibleLogicalRangeChange: jest.fn(),
    unsubscribeVisibleLogicalRangeChange: jest.fn(),
  }
  const createdLines: MockLine[] = []
  const removed: MockLine[] = []
  const candle = {
    setData: jest.fn(),
    setMarkers: jest.fn(),
    applyOptions: jest.fn(),
    attachPrimitive: jest.fn(),
    detachPrimitive: jest.fn(),
  }
  const shared = jest.requireActual('../tests/chartMockShared')
  return {
    ...shared.seriesDefs,
    createSeriesMarkers: shared.createSeriesMarkers,
    ColorType: { Solid: 'solid' },
    createChart: jest.fn(() => ({
      addSeries: shared.dispatchAddSeries({
        candle: () => {
          // Bind the module-level handles when the chart mounts (after load).
          mockFitContent = fitContent
          mockCreatedLines = createdLines
          mockRemoved = removed
          return candle
        },
        line: (opts: { priceScaleId?: string }) => {
          const s: MockLine = { setData: jest.fn(), applyOptions: jest.fn(), _opts: opts }
          createdLines.push(s)
          return s
        },
        histogram: () => ({ setData: jest.fn(), applyOptions: jest.fn() }),
      }),
      priceScale: jest.fn(() => ({ applyOptions: jest.fn() })),
      removeSeries: jest.fn((s: MockLine) => {
        removed.push(s)
      }),
      remove: jest.fn(),
      applyOptions: jest.fn(),
      timeScale: () => timeScale,
      subscribeClick: jest.fn(),
      unsubscribeClick: jest.fn(),
      subscribeCrosshairMove: jest.fn(),
      unsubscribeCrosshairMove: jest.fn(),
    })),
  }
})

const BARS: Bar[] = Array.from({ length: 5 }, (_, i) => ({
  symbol: 'BTC-USD',
  timeframe: '1d',
  event_ts: new Date(Date.UTC(2026, 3, 1 + i)).toISOString(),
  open: 100,
  high: 102,
  low: 99,
  close: 101,
  volume: 1000,
  source: 'fixture',
}))
const OVERLAYS: OverlaySpec[] = [{ kind: 'ema', period: 20 }]

// The agent overlay line series are the ones with no explicit priceScaleId.
function overlayLines(): MockLine[] {
  return mockCreatedLines.filter((s) => s._opts.priceScaleId === undefined)
}

beforeEach(() => {
  jest.clearAllMocks()
  mockFitContent = undefined
  // The factory's tracking arrays are closure singletons (created once at module
  // load); truncate the bound references so each test sees only its own series.
  mockCreatedLines.length = 0
  mockRemoved.length = 0
})

it('fits the view once on initial load', () => {
  render(<CandlestickChart bars={BARS} overlays={OVERLAYS} />)
  expect(mockFitContent).toBeDefined()
  expect(mockFitContent).toHaveBeenCalledTimes(1)
})

it('does NOT refit when a legend toggle hides/shows an overlay, but still removes/re-adds it', () => {
  render(<CandlestickChart bars={BARS} overlays={OVERLAYS} />)
  expect(mockFitContent).toHaveBeenCalledTimes(1)
  const ema = overlayLines()[0]

  // Hide the overlay via its legend checkbox.
  fireEvent.click(within(screen.getByTestId('layer-row:overlay:ema:20')).getByRole('checkbox'))
  expect(mockRemoved).toContain(ema) // series removed…
  expect(mockFitContent).toHaveBeenCalledTimes(1) // …but the view was NOT refit

  // Show it again.
  const beforeReadd = overlayLines().length
  fireEvent.click(within(screen.getByTestId('layer-row:overlay:ema:20')).getByRole('checkbox'))
  expect(overlayLines().length).toBe(beforeReadd + 1) // re-added…
  expect(mockFitContent).toHaveBeenCalledTimes(1) // …still no refit
})

it('DOES refit on a genuine data change (a new bars array)', () => {
  const { rerender } = render(<CandlestickChart bars={BARS} overlays={OVERLAYS} />)
  expect(mockFitContent).toHaveBeenCalledTimes(1)

  // A new bars reference (e.g. symbol/timeframe/range change) refits.
  const newBars = BARS.map((b) => ({ ...b }))
  rerender(<CandlestickChart bars={newBars} overlays={OVERLAYS} />)
  expect(mockFitContent).toHaveBeenCalledTimes(2)
})
