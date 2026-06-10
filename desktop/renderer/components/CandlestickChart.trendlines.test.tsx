/**
 * Plan 0052 phase 4 done-when: trendline rendering + its legend row.
 *
 * Drives the REAL component with a mocked `lightweight-charts` whose
 * `attachPrimitive` captures the trendline primitive (identified by its
 * `setTrendlines`) AND invokes its `attached()` with stubbed time/price scales,
 * so we can assert against the primitive's actual segment state: a forming hit
 * renders dashed and a confirmed hit solid; trendlines produce a pane view and
 * a "Trendlines" legend row; unchecking the row removes the lines and
 * re-checking restores them; and no trendlines → no row, no view.
 *
 * The pixel-coordinate math itself is covered canvas-free in
 * `lib/trendlines.test.ts`.
 */
import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { SeriesAttachedParameter, Time } from 'lightweight-charts'

import { CandlestickChart } from './CandlestickChart'
import type { TrendlinePrimitive } from '../lib/trendlines'
import type { Bar } from '../types/sidecar/bar'
import type { TrendlineSpec } from '../types/events'

let mockTrendlinePrimitive: TrendlinePrimitive | null = null

jest.mock('lightweight-charts', () => {
  // Stubbed scales: every time maps to a fixed x, every price to y = price.
  const timeScale = {
    fitContent: jest.fn(),
    getVisibleLogicalRange: jest.fn(() => null),
    setVisibleLogicalRange: jest.fn(),
    subscribeVisibleLogicalRangeChange: jest.fn(),
    unsubscribeVisibleLogicalRangeChange: jest.fn(),
    timeToCoordinate: jest.fn((t: number) => (t > 0 ? 50 : null)),
  }
  const series = {
    setData: jest.fn(),
    setMarkers: jest.fn(),
    applyOptions: jest.fn(),
    priceToCoordinate: jest.fn((p: number) => p),
    attachPrimitive: jest.fn((p: TrendlinePrimitive) => {
      // Two primitives attach (span band + trendlines); keep the trendline one,
      // and invoke `attached` (as the real library would) with the stub scales
      // so `currentSegments()` is assertable.
      p.attached?.({
        chart: { timeScale: () => timeScale },
        series,
        requestUpdate: jest.fn(),
      } as unknown as SeriesAttachedParameter<Time>)
      if (typeof (p as { setTrendlines?: unknown }).setTrendlines === 'function') {
        mockTrendlinePrimitive = p
      }
    }),
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
      timeScale: () => timeScale,
      subscribeClick: jest.fn(),
      unsubscribeClick: jest.fn(),
      subscribeCrosshairMove: jest.fn(),
      unsubscribeCrosshairMove: jest.fn(),
    })),
  }
})

const BARS: Bar[] = Array.from({ length: 3 }, (_, i) => ({
  symbol: 'ES=F',
  timeframe: '1d',
  event_ts: `2026-04-1${i + 3}T00:00:00+00:00`,
  open: 100,
  high: 102,
  low: 99,
  close: 101,
  volume: 1_000_000,
  source: 'fixture',
}))

/** A forming H&S neckline (dashed) + a confirmed double-top neckline (solid). */
const FORMING: TrendlineSpec = {
  points: [
    { ts: '2026-04-13T00:00:00+00:00', price: 100 },
    { ts: '2026-04-15T00:00:00+00:00', price: 104 },
  ],
  role: 'neckline',
  style: 'dashed',
  pattern: 'head_shoulders',
}
const CONFIRMED: TrendlineSpec = {
  points: [
    { ts: '2026-04-13T00:00:00+00:00', price: 96 },
    { ts: '2026-04-15T00:00:00+00:00', price: 98 },
  ],
  role: 'neckline',
  style: 'solid',
  pattern: 'double_top',
}

beforeEach(() => {
  mockTrendlinePrimitive = null
})

it('draws trendlines (one pane view) and a Trendlines legend row', () => {
  render(<CandlestickChart bars={BARS} trendlines={[FORMING, CONFIRMED]} />)
  expect(mockTrendlinePrimitive).not.toBeNull()
  expect(mockTrendlinePrimitive?.paneViews()).toHaveLength(1)
  expect(screen.getByTestId('layer-row:trendlines')).toBeInTheDocument()
})

it('renders a forming hit dashed and a confirmed hit solid (segment style state)', () => {
  render(<CandlestickChart bars={BARS} trendlines={[FORMING, CONFIRMED]} />)
  const segments = mockTrendlinePrimitive?.currentSegments() ?? []
  expect(segments).toHaveLength(2)
  expect(segments.map((s) => s.dashed)).toEqual([true, false])
})

it('shows NO trendline row and NO pane view when there are no trendlines', () => {
  render(<CandlestickChart bars={BARS} />)
  expect(mockTrendlinePrimitive).not.toBeNull() // attached once, idle
  expect(mockTrendlinePrimitive?.paneViews()).toHaveLength(0)
  expect(screen.queryByTestId('layer-row:trendlines')).not.toBeInTheDocument()
})

it('unchecking the trendline row removes the lines; re-checking restores them', () => {
  render(<CandlestickChart bars={BARS} trendlines={[FORMING, CONFIRMED]} />)
  expect(mockTrendlinePrimitive?.paneViews()).toHaveLength(1)

  const checkbox = (): HTMLElement =>
    within(screen.getByTestId('layer-row:trendlines')).getByRole('checkbox')

  fireEvent.click(checkbox())
  expect(mockTrendlinePrimitive?.paneViews()).toHaveLength(0)

  fireEvent.click(checkbox())
  expect(mockTrendlinePrimitive?.paneViews()).toHaveLength(1)
})
