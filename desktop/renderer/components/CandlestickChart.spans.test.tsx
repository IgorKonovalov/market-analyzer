/**
 * Plan 0049 phase 7 done-when: multi-bar pattern span rendering, updated for the
 * Plan 0071 phase-2 reconciliation — spans no longer own a standalone "Pattern
 * spans" legend row; they fold into the candlestick layer and gate with their
 * (pattern type, direction) group.
 *
 * Drives the REAL component with a mocked `lightweight-charts` whose
 * `attachPrimitive` captures the span primitive, so we can assert: a multi-bar
 * marker (a morning_star span) produces a span band (one pane view) plus the
 * candlestick legend (master + its group row); a single-bar marker (a doji, no
 * span) produces NO band (the branch on `span_*` presence) yet still legends as a
 * marker group; and toggling the group off removes the band (spans follow the
 * group now, not a separate span toggle).
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
  const shared = jest.requireActual('../tests/chartMockShared')
  return {
    ...shared.seriesDefs,
    createSeriesMarkers: shared.createSeriesMarkers,
    ColorType: { Solid: 'solid' },
    createChart: jest.fn(() => ({
      ...shared.paneStubs,
      addSeries: shared.dispatchAddSeries({
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

it('attaches the span primitive and draws a band + candlestick legend for a multi-bar pattern', () => {
  render(<CandlestickChart bars={BARS} annotations={[SPAN_MARKER]} />)
  expect(mockAttachedPrimitive).not.toBeNull()
  // The 3-bar morning_star is the sole (and most-recent) group → enabled by
  // default → fed as one span → one pane view (the band).
  expect(mockAttachedPrimitive?.paneViews()).toHaveLength(1)
  expect(screen.getByTestId('layer-row:candles-master')).toBeInTheDocument()
  expect(screen.getByTestId('layer-row:candles:morning_star|bullish_marker')).toBeInTheDocument()
})

it('draws NO band for a single-bar pattern, but still legends it as a marker group', () => {
  render(<CandlestickChart bars={BARS} annotations={[SINGLE_BAR_MARKER]} />)
  expect(mockAttachedPrimitive).not.toBeNull()
  // No span_* → no band…
  expect(mockAttachedPrimitive?.paneViews()).toHaveLength(0)
  // …but the doji is still a candlestick marker, so its group row lists.
  expect(screen.getByTestId('layer-row:candles:doji|neutral_marker')).toBeInTheDocument()
})

it('toggling the group off removes the band (spans follow the group, not a separate row)', () => {
  render(<CandlestickChart bars={BARS} annotations={[SPAN_MARKER]} />)
  expect(mockAttachedPrimitive?.paneViews()).toHaveLength(1)

  // No standalone "Pattern spans" row exists anymore; the span is gated by its
  // (morning_star, bullish) group. Unchecking that group removes the band.
  expect(screen.queryByTestId('layer-row:spans')).not.toBeInTheDocument()
  fireEvent.click(
    within(screen.getByTestId('layer-row:candles:morning_star|bullish_marker')).getByRole(
      'checkbox',
    ),
  )
  expect(mockAttachedPrimitive?.paneViews()).toHaveLength(0)
})
