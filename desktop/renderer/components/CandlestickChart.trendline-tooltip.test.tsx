/**
 * Plan 0067 phase 2 done-when: a crosshair over a trendline renders a tooltip
 * showing the line's pattern name + state.
 *
 * Drives the REAL component with a mocked `lightweight-charts` that both (a)
 * attaches the trendline primitive with stubbed time/price scales (so
 * `hitTestTrendline` maps a pixel to the drawn segment) and (b) captures the
 * `subscribeCrosshairMove` handler so the test can move the crosshair over a
 * line. A line can extend past the last bar, so the tooltip must appear even
 * when `param.time` is undefined — the crosshair is gated on `point` only.
 */
import '@testing-library/jest-dom'
import { act, render, screen } from '@testing-library/react'
import type { MouseEventParams, SeriesAttachedParameter, Time } from 'lightweight-charts'

import { CandlestickChart } from './CandlestickChart'
import type { TrendlinePrimitive } from '../lib/trendlines'
import type { Bar } from '../types/sidecar/bar'
import type { TrendlineSpec } from '../types/events'

let crosshairHandler: ((param: MouseEventParams) => void) | null = null

jest.mock('lightweight-charts', () => {
  // BARS at logical 0/1/2, x 100/160/220 (grid-snapped); price maps to y = price.
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
      p.attached?.({
        chart: { timeScale: () => timeScale },
        series,
        requestUpdate: jest.fn(),
      } as unknown as SeriesAttachedParameter<Time>)
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
      subscribeCrosshairMove: jest.fn((handler: (param: MouseEventParams) => void) => {
        crosshairHandler = handler
      }),
      unsubscribeCrosshairMove: jest.fn(() => {
        crosshairHandler = null
      }),
    })),
  }
})

const BARS: Bar[] = Array.from({ length: 3 }, (_, i) => ({
  symbol: 'ES=F',
  timeframe: '1d',
  event_ts: `2026-04-1${i + 3}T00:00:00+00:00`,
  open: 100,
  high: 105,
  low: 99,
  close: 101,
  volume: 1_000_000,
  source: 'fixture',
}))

// Draws (100,100)-(220,104) under the stub scales; midpoint (160,102).
const FORMING: TrendlineSpec = {
  points: [
    { ts: '2026-04-13T00:00:00+00:00', price: 100 },
    { ts: '2026-04-15T00:00:00+00:00', price: 104 },
  ],
  role: 'neckline',
  style: 'dashed',
  pattern: 'head_shoulders',
}

function moveCrosshair(opts: { time?: number; point?: { x: number; y: number } }): void {
  act(() => {
    crosshairHandler?.({
      time: opts.time as unknown as MouseEventParams['time'],
      point: opts.point as unknown as MouseEventParams['point'],
      seriesData: new Map(),
    } as MouseEventParams)
  })
}

afterEach(() => {
  crosshairHandler = null
})

it('shows the pattern + state when the crosshair is over a trendline', () => {
  render(<CandlestickChart bars={BARS} trendlines={[FORMING]} />)
  expect(crosshairHandler).not.toBeNull()

  // Over the line's midpoint (no bar time needed — the line drives the tooltip).
  moveCrosshair({ point: { x: 160, y: 102 } })
  const tooltip = screen.getByTestId('chart-tooltip')
  expect(tooltip).toHaveTextContent('Head & shoulders — forming')
  expect(screen.getByTestId('tooltip-trendline')).toBeInTheDocument()
})

it('shows nothing when the cursor is away from every line', () => {
  render(<CandlestickChart bars={BARS} trendlines={[FORMING]} />)
  moveCrosshair({ point: { x: 160, y: 300 } })
  expect(screen.queryByTestId('chart-tooltip')).not.toBeInTheDocument()
})
