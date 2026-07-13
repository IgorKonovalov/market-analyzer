/**
 * Plan 0052 phase 4 done-when: trendline rendering + its legend row.
 *
 * Drives the REAL component with a mocked `lightweight-charts` whose
 * `attachPrimitive` captures the trendline primitive (identified by its
 * `setTrendlines`) AND invokes its `attached()` with stubbed time/price scales,
 * so we can assert against the primitive's actual segment state: a forming hit
 * renders dashed and a confirmed hit solid; trendlines produce a pane view and
 * one grouped legend row per (pattern type, state) with an instance count
 * (Plan 0067 phase 3); unchecking a group's row removes exactly that group's
 * lines and re-checking restores them; hovering a row highlights that group; and
 * no trendlines → no rows, no view.
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
  // Stubbed scales modelling the real contract (Plan 0064 phase 1): the 3 loaded
  // BARS sit at logical 0/1/2 and x 100/160/220; `timeToCoordinate` resolves ONLY
  // those exact bar times (null-for-off-grid), while `logicalToCoordinate`
  // interpolates/extrapolates linearly — so off-grid anchors route through the
  // fallback. Price maps to y = price.
  const toSec = (iso: string): number => Math.floor(new Date(iso).getTime() / 1000)
  const BAR_TIMES = [
    toSec('2026-04-13T00:00:00+00:00'),
    toSec('2026-04-14T00:00:00+00:00'),
    toSec('2026-04-15T00:00:00+00:00'),
  ]
  const timeScale = {
    fitContent: jest.fn(),
    getVisibleLogicalRange: jest.fn(() => null),
    setVisibleLogicalRange: jest.fn(),
    subscribeVisibleLogicalRangeChange: jest.fn(),
    unsubscribeVisibleLogicalRangeChange: jest.fn(),
    timeToCoordinate: jest.fn((t: number) => {
      const i = BAR_TIMES.indexOf(t)
      return i >= 0 ? 100 + 60 * i : null
    }),
    logicalToCoordinate: jest.fn((l: number) => 100 + 60 * l),
  }
  const series = {
    setData: jest.fn(),
    setMarkers: jest.fn(),
    applyOptions: jest.fn(),
    data: jest.fn(() => BAR_TIMES.map((t) => ({ time: t }))),
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
  const shared = jest.requireActual('../tests/chartMockShared')
  return {
    ...shared.seriesDefs,
    createSeriesMarkers: shared.createSeriesMarkers,
    ColorType: { Solid: 'solid' },
    createChart: jest.fn(() => ({
      addSeries: shared.dispatchAddSeries({
        candle: () => series,
        line: () => ({ setData: jest.fn(), applyOptions: jest.fn() }),
      }),
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

it('draws trendlines (one pane view) and a legend row per (pattern type, state)', () => {
  render(<CandlestickChart bars={BARS} trendlines={[FORMING, CONFIRMED]} />)
  expect(mockTrendlinePrimitive).not.toBeNull()
  expect(mockTrendlinePrimitive?.paneViews()).toHaveLength(1)
  expect(screen.getByTestId('layer-row:trendlines:head_shoulders|dashed')).toBeInTheDocument()
  expect(screen.getByTestId('layer-row:trendlines:double_top|solid')).toBeInTheDocument()
})

it('renders a forming hit dashed and a confirmed hit solid (segment style state)', () => {
  render(<CandlestickChart bars={BARS} trendlines={[FORMING, CONFIRMED]} />)
  const segments = mockTrendlinePrimitive?.currentSegments() ?? []
  expect(segments).toHaveLength(2)
  expect(segments.map((s) => s.dashed)).toEqual([true, false])
})

// Plan 0064 phase 1: a trendline whose endpoints are OFF the loaded bar grid
// (one between bars, one past the last bar) — the live failure. Pre-fix
// `timeToCoordinate` null-skipped both endpoints and drew nothing; the bar-grid
// logical fallback now resolves them so the line still strokes.
const OFF_GRID: TrendlineSpec = {
  points: [
    { ts: '2026-04-14T12:00:00+00:00', price: 100 }, // between bars 14 and 15
    { ts: '2026-04-18T00:00:00+00:00', price: 104 }, // 3 days past the last bar
  ],
  role: 'neckline',
  style: 'solid',
  pattern: 'head_shoulders',
}

it('draws an off-grid / beyond-range trendline via the bar-grid fallback (Plan 0064 phase 1)', () => {
  render(<CandlestickChart bars={BARS} trendlines={[OFF_GRID]} />)
  const segments = mockTrendlinePrimitive?.currentSegments() ?? []
  expect(segments).toHaveLength(1)
})

it('shows NO trendline rows and NO pane view when there are no trendlines', () => {
  render(<CandlestickChart bars={BARS} />)
  expect(mockTrendlinePrimitive).not.toBeNull() // attached once, idle
  expect(mockTrendlinePrimitive?.paneViews()).toHaveLength(0)
  expect(screen.queryByTestId('layer-row:trendlines:head_shoulders|dashed')).not.toBeInTheDocument()
})

it("unchecking a group's row removes exactly that group's lines; re-checking restores them", () => {
  render(<CandlestickChart bars={BARS} trendlines={[FORMING, CONFIRMED]} />)
  // Two groups, one line each → two drawn segments.
  expect(mockTrendlinePrimitive?.currentSegments()).toHaveLength(2)

  const checkbox = (): HTMLElement =>
    within(screen.getByTestId('layer-row:trendlines:head_shoulders|dashed')).getByRole('checkbox')

  fireEvent.click(checkbox())
  // Only the head_shoulders forming group is removed; the confirmed double-top
  // line remains (group-granular visibility).
  expect(mockTrendlinePrimitive?.currentSegments()).toHaveLength(1)

  fireEvent.click(checkbox())
  expect(mockTrendlinePrimitive?.currentSegments()).toHaveLength(2)
})

// Two distinct-geometry forming H&S necklines (no solid twin → both survive
// dedupe) collapse into ONE (head_shoulders, forming) row with count 2.
const FORMING_B: TrendlineSpec = {
  points: [
    { ts: '2026-04-13T00:00:00+00:00', price: 90 },
    { ts: '2026-04-15T00:00:00+00:00', price: 94 },
  ],
  role: 'neckline',
  style: 'dashed',
  pattern: 'head_shoulders',
}

it('lists one row per (pattern type, state) with the instance count', () => {
  render(<CandlestickChart bars={BARS} trendlines={[FORMING, FORMING_B, CONFIRMED]} />)
  expect(screen.getByTestId('layer-count:trendlines:head_shoulders|dashed')).toHaveTextContent('2')
  expect(screen.getByTestId('layer-count:trendlines:double_top|solid')).toHaveTextContent('1')
  // The row also names the pattern + state.
  expect(screen.getByTestId('layer-row:trendlines:double_top|solid')).toHaveTextContent(
    'Double top (confirmed)',
  )
})

it("hovering a group's row highlights that group; leaving clears it", () => {
  render(<CandlestickChart bars={BARS} trendlines={[FORMING, CONFIRMED]} />)
  const row = screen.getByTestId('layer-row:trendlines:head_shoulders|dashed')

  fireEvent.mouseEnter(row)
  expect(mockTrendlinePrimitive?.highlightedGroup()).toBe('head_shoulders|dashed')

  fireEvent.mouseLeave(row)
  expect(mockTrendlinePrimitive?.highlightedGroup()).toBeNull()
})
