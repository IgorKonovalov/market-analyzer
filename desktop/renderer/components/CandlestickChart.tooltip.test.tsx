/**
 * Plan 0047 phase 8 done-when: the chart's hover tooltip.
 *
 * Drives the REAL component with a mocked `lightweight-charts` that captures the
 * `subscribeCrosshairMove` handler, then invokes it: a crosshair on a bar that
 * carries a persisted annotation shows that annotation's label; moving away
 * (no crosshair time) clears it. The tooltip reads only from props already in
 * renderer state — the mock makes no sidecar call.
 */
import '@testing-library/jest-dom'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import type { MouseEventParams, UTCTimestamp } from 'lightweight-charts'

import { CandlestickChart } from './CandlestickChart'
import type { Annotation } from '../types/sidecar/annotation'
import type { ChartMarker } from '../lib/markers'
import type { Bar } from '../types/sidecar/bar'
import { localize, term } from '../glossary/types'

let crosshairHandler: ((param: MouseEventParams) => void) | null = null

jest.mock('lightweight-charts', () => ({
  ColorType: { Solid: 'solid' },
  createChart: jest.fn(() => ({
    addCandlestickSeries: jest.fn(() => ({
      setData: jest.fn(),
      setMarkers: jest.fn(),
      applyOptions: jest.fn(),
      attachPrimitive: jest.fn(),
      detachPrimitive: jest.fn(),
    })),
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
    subscribeCrosshairMove: jest.fn((handler: (param: MouseEventParams) => void) => {
      crosshairHandler = handler
    }),
    unsubscribeCrosshairMove: jest.fn(() => {
      crosshairHandler = null
    }),
  })),
}))

const APR15_TS = (Date.UTC(2026, 3, 15) / 1000) as UTCTimestamp

const BARS: Bar[] = [
  {
    symbol: 'AAPL',
    timeframe: '1d',
    event_ts: '2026-04-15T00:00:00+00:00',
    open: 100,
    high: 102,
    low: 99,
    close: 101,
    volume: 1_000_000,
    source: 'fixture',
  },
]

const ANNOTATION: Annotation = {
  id: 'ann-1',
  symbol: 'AAPL',
  timeframe: '1d',
  event_ts: '2026-04-15T00:00:00+00:00',
  kind: 'bullish_marker',
  label: 'hammer at support',
  agent_id: 'test',
  created_at: '2026-04-15T01:00:00+00:00',
}

function moveCrosshair(opts: { time?: UTCTimestamp; point?: { x: number; y: number } }): void {
  act(() => {
    crosshairHandler?.({
      time: opts.time,
      // `point` carries branded `Coordinate` numbers; plain numbers are fine for
      // the handler at runtime (it only reads `.x`/`.y`).
      point: opts.point as unknown as MouseEventParams['point'],
      seriesData: new Map(),
    } as MouseEventParams)
  })
}

afterEach(() => {
  crosshairHandler = null
})

it('shows the annotation label when the crosshair is on its bar, and clears on move-away', () => {
  render(<CandlestickChart bars={BARS} annotations={[ANNOTATION]} />)
  expect(crosshairHandler).not.toBeNull()

  // No tooltip until the crosshair lands on the bar.
  expect(screen.queryByTestId('chart-tooltip')).not.toBeInTheDocument()

  moveCrosshair({ time: APR15_TS, point: { x: 40, y: 60 } })
  const tooltip = screen.getByTestId('chart-tooltip')
  expect(tooltip).toHaveTextContent('hammer at support')

  // Pointer leaves the chart (no crosshair time) → tooltip clears.
  moveCrosshair({ time: undefined, point: undefined })
  expect(screen.queryByTestId('chart-tooltip')).not.toBeInTheDocument()
})

it('shows no tooltip when hovering a bar with no marker and no overlay', () => {
  render(<CandlestickChart bars={BARS} annotations={[ANNOTATION]} />)
  // A different bar time — no annotation there.
  moveCrosshair({ time: (Date.UTC(2026, 3, 20) / 1000) as UTCTimestamp, point: { x: 80, y: 60 } })
  expect(screen.queryByTestId('chart-tooltip')).not.toBeInTheDocument()
})

// Plan 0071 follow-up: a sweep marker names its pattern, so the hover shows the
// pattern's display name (not the raw wire token or a bare direction word).
const SWEEP_MARKER: ChartMarker = {
  event_ts: '2026-04-15T00:00:00+00:00',
  kind: 'bullish_marker',
  pattern: 'bullish_engulfing',
}

it('shows the candlestick pattern name and its meaning on hover (Plan 0071 follow-up / Plan 0085)', () => {
  render(<CandlestickChart bars={BARS} annotations={[SWEEP_MARKER]} />)
  moveCrosshair({ time: APR15_TS, point: { x: 40, y: 60 } })
  expect(screen.getByTestId('chart-tooltip')).toHaveTextContent('Bullish engulfing')
  // Plan 0085: a single hovered marker also discloses its what-it-means line.
  expect(screen.getByTestId('tooltip-marker-meaning')).toHaveTextContent(
    localize(term('bullish_engulfing')!.whatItMeans, 'en'),
  )
})

it('shows NO hover for a group toggled off — no arrow is drawn there (bug fix)', () => {
  render(<CandlestickChart bars={BARS} annotations={[SWEEP_MARKER]} />)
  // The sole group is drawn by default → its bar hovers.
  moveCrosshair({ time: APR15_TS, point: { x: 40, y: 60 } })
  expect(screen.getByTestId('chart-tooltip')).toBeInTheDocument()

  // Toggle the group off → its arrow is gone, so hovering that bar shows nothing.
  fireEvent.click(
    within(screen.getByTestId('layer-row:candles:bullish_engulfing|bullish_marker')).getByRole(
      'checkbox',
    ),
  )
  moveCrosshair({ time: APR15_TS, point: { x: 40, y: 60 } })
  expect(screen.queryByTestId('chart-tooltip')).not.toBeInTheDocument()
})
