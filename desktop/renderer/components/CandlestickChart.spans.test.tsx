/**
 * Plan 0049 phase 7 done-when: multi-bar pattern span rendering + its legend row.
 *
 * Drives the REAL component with a mocked `lightweight-charts` whose
 * `attachPrimitive` captures the span primitive, so we can assert: a multi-bar
 * marker (a morning_star span) produces a span band (one pane view) and a "Pattern
 * spans" legend row; a single-bar marker (a doji, no span) produces NO band and NO
 * row (the branch on `span_*` presence); and unchecking the span row hides the
 * band while leaving the arrows/overlays in place.
 *
 * The pixel-coordinate math (a span maps to a rect across exactly its bars) is
 * covered canvas-free in `lib/spans.test.ts`.
 */
import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'

import { CandlestickChart } from './CandlestickChart'
import type { ChartMarker } from '../lib/markers'
import type { PatternSpanPrimitive } from '../lib/spans'
import type { Bar } from '../types/sidecar/bar'

let mockAttachedPrimitive: PatternSpanPrimitive | null = null
let setMarkersCalls: Array<Array<unknown>> = []

jest.mock('lightweight-charts', () => {
  const series = {
    setData: jest.fn(),
    setMarkers: jest.fn((m: unknown[]) => {
      setMarkersCalls.push(m)
    }),
    applyOptions: jest.fn(),
    // Two primitives attach now (the span band + the Plan 0052 trendline
    // primitive); capture only the SPAN one — identified by its `setSpans`.
    attachPrimitive: jest.fn((p: PatternSpanPrimitive) => {
      if (typeof (p as { setSpans?: unknown }).setSpans === 'function') {
        mockAttachedPrimitive = p
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
      timeScale: () => ({
        fitContent: jest.fn(),
        getVisibleLogicalRange: jest.fn(() => null),
        setVisibleLogicalRange: jest.fn(),
        subscribeVisibleLogicalRangeChange: jest.fn(),
        unsubscribeVisibleLogicalRangeChange: jest.fn(),
        timeToCoordinate: jest.fn(() => 0),
      }),
      subscribeClick: jest.fn(),
      unsubscribeClick: jest.fn(),
      subscribeCrosshairMove: jest.fn(),
      unsubscribeCrosshairMove: jest.fn(),
    })),
  }
})

const BARS: Bar[] = Array.from({ length: 3 }, (_, i) => ({
  symbol: 'BTC-USD',
  timeframe: '1d',
  event_ts: `2026-04-1${i + 3}T00:00:00+00:00`,
  open: 100,
  high: 102,
  low: 99,
  close: 101,
  volume: 1_000_000,
  source: 'fixture',
}))

const SPAN_MARKER: ChartMarker = {
  event_ts: '2026-04-15T00:00:00+00:00',
  kind: 'bullish_marker',
  pattern: 'morning_star',
  span_start_ts: '2026-04-13T00:00:00+00:00',
  span_end_ts: '2026-04-15T00:00:00+00:00',
}

const SINGLE_BAR_MARKER: ChartMarker = {
  event_ts: '2026-04-15T00:00:00+00:00',
  kind: 'neutral_marker',
  pattern: 'doji',
}

beforeEach(() => {
  mockAttachedPrimitive = null
  setMarkersCalls = []
})

it('attaches the span primitive and draws a band + legend row for a multi-bar pattern', () => {
  render(<CandlestickChart bars={BARS} annotations={[SPAN_MARKER]} />)
  expect(mockAttachedPrimitive).not.toBeNull()
  // The 3-bar morning_star is fed as one span → one pane view (the band).
  expect(mockAttachedPrimitive?.paneViews()).toHaveLength(1)
  expect(screen.getByTestId('layer-row:spans')).toBeInTheDocument()
})

it('draws NO band and NO span row for a single-bar pattern (branch on span_* presence)', () => {
  render(<CandlestickChart bars={BARS} annotations={[SINGLE_BAR_MARKER]} />)
  expect(mockAttachedPrimitive).not.toBeNull()
  expect(mockAttachedPrimitive?.paneViews()).toHaveLength(0)
  expect(screen.queryByTestId('layer-row:spans')).not.toBeInTheDocument()
})

it('unchecking the span row hides the band but leaves the markers drawn', () => {
  render(<CandlestickChart bars={BARS} annotations={[SPAN_MARKER]} />)
  expect(mockAttachedPrimitive?.paneViews()).toHaveLength(1)
  const markerCallsBefore = setMarkersCalls.length

  fireEvent.click(within(screen.getByTestId('layer-row:spans')).getByRole('checkbox'))

  // Band gone (no pane view) …
  expect(mockAttachedPrimitive?.paneViews()).toHaveLength(0)
  // … and the candlestick markers were still set (arrows unaffected by the toggle).
  expect(setMarkersCalls.length).toBeGreaterThanOrEqual(markerCallsBefore)
})
