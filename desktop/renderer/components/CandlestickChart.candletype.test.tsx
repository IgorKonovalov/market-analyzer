/**
 * Plan 0068 phase 4 done-when: changing the candle series-type REBUILDS the chart
 * instance (the series type is fixed at creation). Selecting Line:
 *   - recreates the main series as a line series (reflected in
 *     `window.__test_chart_render__`),
 *   - re-attaches the span + trendline primitives and re-sets markers on it,
 *   - disposes the old chart instance (no leaked context).
 *
 * lightweight-charts is mocked; `createChart` returns a FRESH fake chart per call
 * (so a rebuild is observable as a second `createChart` + the first chart's
 * `remove`). Every series fake carries the full surface the component drives on
 * the main series (attachPrimitive / setMarkers / createPriceLine / update).
 */
import '@testing-library/jest-dom'

import { act, render } from '@testing-library/react'
import { createChart } from 'lightweight-charts'

import { CandlestickChart } from './CandlestickChart'
import { resetChartStyle, setCandleType } from '../lib/chartStyle'
import { setTheme } from '../lib/theme'
import type { Bar } from '../types/sidecar/bar'

interface FakeSeries {
  kind: string
  setData: jest.Mock
  setMarkers: jest.Mock
  applyOptions: jest.Mock
  attachPrimitive: jest.Mock
  detachPrimitive: jest.Mock
  createPriceLine: jest.Mock
  removePriceLine: jest.Mock
  update: jest.Mock
  _opts: Record<string, unknown>
}

interface FakeChart {
  remove: jest.Mock
  [method: string]: unknown
}

let allSeries: FakeSeries[] = []
let charts: FakeChart[] = []

function makeSeries(kind: string, opts: unknown): FakeSeries {
  const s: FakeSeries = {
    kind,
    setData: jest.fn(),
    setMarkers: jest.fn(),
    applyOptions: jest.fn(),
    attachPrimitive: jest.fn(),
    detachPrimitive: jest.fn(),
    createPriceLine: jest.fn(),
    removePriceLine: jest.fn(),
    update: jest.fn(),
    _opts: (opts ?? {}) as Record<string, unknown>,
  }
  allSeries.push(s)
  return s
}

function buildChart(): FakeChart {
  const chart: FakeChart = {
    addCandlestickSeries: jest.fn((o: unknown) => makeSeries('candlestick', o)),
    addBarSeries: jest.fn((o: unknown) => makeSeries('bar', o)),
    addLineSeries: jest.fn((o: unknown) => makeSeries('line', o)),
    addAreaSeries: jest.fn((o: unknown) => makeSeries('area', o)),
    addHistogramSeries: jest.fn((o: unknown) => makeSeries('histogram', o)),
    priceScale: jest.fn(() => ({ applyOptions: jest.fn() })),
    removeSeries: jest.fn(),
    remove: jest.fn(),
    applyOptions: jest.fn(),
    timeScale: () => ({
      fitContent: jest.fn(),
      getVisibleRange: jest.fn(() => null),
      getVisibleLogicalRange: jest.fn(() => null),
      setVisibleLogicalRange: jest.fn(),
      subscribeVisibleLogicalRangeChange: jest.fn(),
      unsubscribeVisibleLogicalRangeChange: jest.fn(),
    }),
    subscribeClick: jest.fn(),
    unsubscribeClick: jest.fn(),
    subscribeCrosshairMove: jest.fn(),
    unsubscribeCrosshairMove: jest.fn(),
  }
  charts.push(chart)
  return chart
}

jest.mock('lightweight-charts', () => ({
  ColorType: { Solid: 'solid' },
  TickMarkType: { Year: 0 },
  createChart: jest.fn(),
}))

const createChartMock = createChart as unknown as jest.Mock

beforeEach(() => {
  window.localStorage.clear()
  resetChartStyle()
  delete document.documentElement.dataset.theme
  allSeries = []
  charts = []
  createChartMock.mockReset()
  createChartMock.mockImplementation(() => buildChart())
})

afterEach(() => {
  act(() => setTheme('system'))
  resetChartStyle()
  jest.restoreAllMocks()
})

const BARS: Bar[] = Array.from({ length: 20 }, (_, i) => {
  const d = new Date('2026-04-01T00:00:00+00:00')
  d.setUTCDate(d.getUTCDate() + i)
  return {
    symbol: 'AAPL',
    timeframe: '1d',
    event_ts: d.toISOString(),
    open: 100 + i,
    high: 101 + i,
    low: 99 + i,
    close: 100 + i,
    volume: 1000,
    source: 'test',
  }
})

/** The main series of a chart is the one the component attaches primitives to
 * (span + trendline) — overlay/volume line series never get a primitive. */
function primitiveBearingSeries(): FakeSeries[] {
  return allSeries.filter((s) => s.attachPrimitive.mock.calls.length > 0)
}

describe('CandlestickChart — candle series-type switch (Plan 0068 phase 4)', () => {
  it('starts as a candlestick main series', () => {
    render(<CandlestickChart bars={BARS} />)
    expect(charts).toHaveLength(1)
    expect(window.__test_chart_render__!.seriesKinds[0]).toEqual({ kind: 'candlestick' })
    const main = primitiveBearingSeries()
    expect(main).toHaveLength(1)
    expect(main[0].kind).toBe('candlestick')
  })

  it('selecting Line rebuilds the chart: new line main series, primitives + markers re-attached, old disposed', () => {
    render(<CandlestickChart bars={BARS} />)
    const firstChart = charts[0]
    expect(createChartMock).toHaveBeenCalledTimes(1)

    act(() => {
      setCandleType('line')
    })

    // Rebuilt: a second chart was created and the first was disposed.
    expect(createChartMock).toHaveBeenCalledTimes(2)
    expect(firstChart.remove).toHaveBeenCalled()

    // The main series is now a line series (reflected in the test hook).
    expect(window.__test_chart_render__!.seriesKinds[0]).toEqual({ kind: 'line' })

    // The new main line series has all three primitives re-attached (span +
    // trendline + ichimoku, Plan 0073 phase 4) and its markers re-set — proving
    // the primitives/markers survived the rebuild.
    const lineMains = primitiveBearingSeries().filter((s) => s.kind === 'line')
    expect(lineMains).toHaveLength(1)
    expect(lineMains[0].attachPrimitive).toHaveBeenCalledTimes(3)
    expect(lineMains[0].setMarkers).toHaveBeenCalled()
    // The line main series was fed data as {time, value} (setData called).
    expect(lineMains[0].setData).toHaveBeenCalled()
  })

  it('switching Candles → Line → Area → Candles rebuilds each time and restores candles', () => {
    render(<CandlestickChart bars={BARS} />)
    act(() => setCandleType('line'))
    act(() => setCandleType('area'))
    act(() => setCandleType('candles'))

    // Four chart instances total (initial + three switches).
    expect(createChartMock).toHaveBeenCalledTimes(4)
    // Back to a candlestick main series.
    expect(window.__test_chart_render__!.seriesKinds[0]).toEqual({ kind: 'candlestick' })
    // Every superseded chart was disposed (all but the current one).
    for (const chart of charts.slice(0, -1)) {
      expect(chart.remove).toHaveBeenCalled()
    }
  })
})
