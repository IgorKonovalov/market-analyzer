/**
 * Plan 0014 phase 3 done-when: CandlestickChart UI gestures (range-select +
 * bar-click), gated on agent mode.
 *
 * Defends the POST/no-POST matrix:
 *   - agent OFF: neither a drag nor a click POSTs.
 *   - agent ON, select-range INACTIVE (default): a drag does NOT POST (pan/zoom),
 *     but a bar click POSTs ui.bar_clicked.
 *   - agent ON, select-range ACTIVE: a drag POSTs ui.range_selected with the
 *     dragged bars' times; Escape mid-drag cancels (no POST).
 *
 * jsdom has no canvas; we mock `lightweight-charts`. The mock captures the
 * subscribeClick handler and exposes a controllable `coordinateToTime` so the
 * drag → bar-time mapping is deterministic. `../api/uiEvents` is mocked so the
 * assertions are on the POST helpers, not the fetch transport.
 */
import '@testing-library/jest-dom'

import { act, createEvent, fireEvent, render, screen } from '@testing-library/react'

import { CandlestickChart } from './CandlestickChart'
import { postBarClicked, postRangeSelected } from '../api/uiEvents'
import type { Bar } from '../types/sidecar/bar'

jest.mock('../api/uiEvents', () => ({
  postRangeSelected: jest.fn().mockResolvedValue(undefined),
  postBarClicked: jest.fn().mockResolvedValue(undefined),
}))

const mockPostRange = postRangeSelected as jest.Mock
const mockPostBar = postBarClicked as jest.Mock

// ---------- lightweight-charts mock --------------------------------------- //

interface FakeCandleSeries {
  setData: jest.Mock
  setMarkers: jest.Mock
  applyOptions: jest.Mock
  attachPrimitive: jest.Mock
  detachPrimitive: jest.Mock
}

let candleSeries: FakeCandleSeries
let clickHandler: ((param: unknown) => void) | null = null
// Maps an x-coordinate to a UTCTimestamp (seconds). Default: identity-ish.
let coordinateToTime: (x: number) => number | null = (x) => 1_714_000_000 + x

jest.mock('lightweight-charts', () => ({
  ...jest.requireActual('../tests/chartMockShared').seriesDefs,
  createSeriesMarkers: jest.requireActual('../tests/chartMockShared').createSeriesMarkers,
  ColorType: { Solid: 'solid' },
  createChart: jest.fn(() => ({
    addSeries: jest.requireActual('../tests/chartMockShared').dispatchAddSeries({
      candle: () => candleSeries,
      line: () => ({ setData: jest.fn(), applyOptions: jest.fn() }),
    }),
    priceScale: jest.fn(() => ({ applyOptions: jest.fn() })),
    removeSeries: jest.fn(),
    remove: jest.fn(),
    applyOptions: jest.fn(),
    timeScale: () => ({
      fitContent: jest.fn(),
      coordinateToTime: (x: number) => coordinateToTime(x),
      // Plan 0030: the chart subscribes to the visible range for lazy paging.
      subscribeVisibleLogicalRangeChange: jest.fn(),
      unsubscribeVisibleLogicalRangeChange: jest.fn(),
    }),
    subscribeClick: jest.fn((handler: (param: unknown) => void) => {
      clickHandler = handler
    }),
    unsubscribeClick: jest.fn(() => {
      clickHandler = null
    }),
    subscribeCrosshairMove: jest.fn(),
    unsubscribeCrosshairMove: jest.fn(),
  })),
}))

beforeEach(() => {
  jest.clearAllMocks()
  candleSeries = {
    setData: jest.fn(),
    setMarkers: jest.fn(),
    applyOptions: jest.fn(),
    attachPrimitive: jest.fn(),
    detachPrimitive: jest.fn(),
  }
  clickHandler = null
  coordinateToTime = (x) => 1_714_000_000 + x
})

// ---------- fixtures ----------------------------------------------------- //

function bar(eventTs: string, close: number): Bar {
  return {
    symbol: 'AAPL',
    timeframe: '1d',
    event_ts: eventTs,
    open: close,
    high: close + 1,
    low: close - 1,
    close,
    volume: 1000,
    source: 'test',
  }
}

const FIXTURE_BARS: Bar[] = Array.from({ length: 10 }, (_, i) => {
  const d = new Date('2026-04-01T00:00:00+00:00')
  d.setUTCDate(d.getUTCDate() + i)
  return bar(d.toISOString(), 100 + i)
})

function renderChart(props: { agentModeEnabled?: boolean }): void {
  render(
    <CandlestickChart
      bars={FIXTURE_BARS}
      symbol="AAPL"
      timeframe="1d"
      agentModeEnabled={props.agentModeEnabled ?? false}
    />,
  )
}

// Pointer events, not mouse — lightweight-charts drives pan via pointer events
// and preventDefaults pointerdown (suppressing compat mouse events), so the real
// gesture is pointer-based. jsdom's PointerEvent drops clientX from the init
// dict (unlike MouseEvent), so force it onto the instance before dispatching.
function firePointer(type: 'pointerDown' | 'pointerMove' | 'pointerUp', clientX: number): void {
  const container = screen.getByTestId('candlestick-chart')
  const event = createEvent[type](container, { pointerId: 1 })
  Object.defineProperty(event, 'clientX', { value: clientX })
  fireEvent(container, event)
}

function dragChart(fromX: number, toX: number): void {
  firePointer('pointerDown', fromX)
  firePointer('pointerMove', toX)
  firePointer('pointerUp', toX)
}

function clickBar(timeSeconds: number, ohlc: { o: number; h: number; l: number; c: number }): void {
  // Simulate lightweight-charts delivering a click on a candle. The handler marks
  // the clicked bar (a React state update), so wrap it in act() — the chart's
  // subscribeClick callback is invoked directly here, bypassing testing-library's
  // implicit act.
  act(() => {
    clickHandler!({
      time: timeSeconds,
      point: { x: 10, y: 10 },
      seriesData: new Map([
        [candleSeries, { open: ohlc.o, high: ohlc.h, low: ohlc.l, close: ohlc.c }],
      ]),
    })
  })
}

/** A click whose seriesData is empty — exercises the bars-prop OHLC fallback. */
function clickEmptyAt(timeSeconds: number): void {
  act(() => {
    clickHandler!({ time: timeSeconds, point: { x: 10, y: 10 }, seriesData: new Map() })
  })
}

function barTime(bar: Bar): number {
  return Math.floor(new Date(bar.event_ts).getTime() / 1000)
}

// ---------- specs --------------------------------------------------------- //

describe('CandlestickChart gestures (Plan 0014)', () => {
  it('agent OFF: a drag does not POST', () => {
    renderChart({ agentModeEnabled: false })
    dragChart(40, 120)
    expect(mockPostRange).not.toHaveBeenCalled()
  })

  it('agent OFF: a bar click does not POST', () => {
    renderChart({ agentModeEnabled: false })
    clickBar(1_714_000_500, { o: 1, h: 2, l: 0, c: 1.5 })
    expect(mockPostBar).not.toHaveBeenCalled()
  })

  it('agent ON, select-range INACTIVE: a drag does not POST (pan/zoom preserved)', () => {
    renderChart({ agentModeEnabled: true })
    // Default mode is pan/zoom — no select-range button pressed.
    dragChart(40, 120)
    expect(mockPostRange).not.toHaveBeenCalled()
  })

  it('agent ON, select-range ACTIVE: a drag POSTs ui.range_selected with the dragged bar times', () => {
    renderChart({ agentModeEnabled: true })
    fireEvent.click(screen.getByTestId('select-range-toggle'))

    dragChart(40, 120)

    expect(mockPostRange).toHaveBeenCalledTimes(1)
    expect(mockPostRange).toHaveBeenCalledWith({
      symbol: 'AAPL',
      timeframe: '1d',
      range_start: new Date((1_714_000_000 + 40) * 1000).toISOString(),
      range_end: new Date((1_714_000_000 + 120) * 1000).toISOString(),
    })
  })

  it('range-select normalises a right-to-left drag (start <= end)', () => {
    renderChart({ agentModeEnabled: true })
    fireEvent.click(screen.getByTestId('select-range-toggle'))

    dragChart(120, 40) // dragged leftwards

    expect(mockPostRange).toHaveBeenCalledWith({
      symbol: 'AAPL',
      timeframe: '1d',
      range_start: new Date((1_714_000_000 + 40) * 1000).toISOString(),
      range_end: new Date((1_714_000_000 + 120) * 1000).toISOString(),
    })
  })

  it('agent ON: a bar click POSTs ui.bar_clicked with the bar OHLC', () => {
    renderChart({ agentModeEnabled: true })
    clickBar(1_714_000_500, { o: 10, h: 12, l: 9, c: 11 })

    expect(mockPostBar).toHaveBeenCalledTimes(1)
    expect(mockPostBar).toHaveBeenCalledWith({
      symbol: 'AAPL',
      timeframe: '1d',
      event_ts: new Date(1_714_000_500 * 1000).toISOString(),
      open: 10,
      high: 12,
      low: 9,
      close: 11,
    })
  })

  it('bar click works while select-range mode is ACTIVE (a click is not a drag)', () => {
    renderChart({ agentModeEnabled: true })
    fireEvent.click(screen.getByTestId('select-range-toggle'))

    clickBar(1_714_000_500, { o: 10, h: 12, l: 9, c: 11 })

    expect(mockPostBar).toHaveBeenCalledTimes(1)
  })

  it('a real range drag does not also fire a bar-click', () => {
    renderChart({ agentModeEnabled: true })
    fireEvent.click(screen.getByTestId('select-range-toggle'))

    dragChart(40, 120) // the pointerup sets the suppress flag
    // lightweight-charts may fire a click on that same release — it must be
    // swallowed, not registered as a bar-click.
    clickBar(1_714_000_080, { o: 1, h: 2, l: 0, c: 1.5 })

    expect(mockPostRange).toHaveBeenCalledTimes(1)
    expect(mockPostBar).not.toHaveBeenCalled()
  })

  it('resolves bar OHLC from the bars prop when seriesData is empty', () => {
    renderChart({ agentModeEnabled: true })
    const bar = FIXTURE_BARS[3]

    clickEmptyAt(barTime(bar))

    expect(mockPostBar).toHaveBeenCalledTimes(1)
    expect(mockPostBar).toHaveBeenCalledWith({
      symbol: 'AAPL',
      timeframe: '1d',
      event_ts: new Date(barTime(bar) * 1000).toISOString(),
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    })
  })

  it('Escape during a range-select cancels the in-progress selection (no POST)', () => {
    renderChart({ agentModeEnabled: true })
    fireEvent.click(screen.getByTestId('select-range-toggle'))

    firePointer('pointerDown', 40)
    fireEvent.keyDown(window, { key: 'Escape' })
    firePointer('pointerUp', 120)

    expect(mockPostRange).not.toHaveBeenCalled()
  })

  it('keeps the selection overlay + a range label after release; Escape clears both', () => {
    renderChart({ agentModeEnabled: true })
    fireEvent.click(screen.getByTestId('select-range-toggle'))

    firePointer('pointerDown', 40)
    firePointer('pointerMove', 120)
    expect(screen.queryByTestId('range-selection-overlay')).toBeInTheDocument()

    firePointer('pointerUp', 120)
    // Persists after release so the user sees what's selected.
    expect(screen.queryByTestId('range-selection-overlay')).toBeInTheDocument()
    const label = screen.getByTestId('range-selection-label')
    expect(label).toHaveTextContent('→')

    // Escape clears the persisted marker + label.
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByTestId('range-selection-overlay')).not.toBeInTheDocument()
    expect(screen.queryByTestId('range-selection-label')).not.toBeInTheDocument()
  })

  it('discards a click-sized drag (no range marker, no POST)', () => {
    renderChart({ agentModeEnabled: true })
    fireEvent.click(screen.getByTestId('select-range-toggle'))

    firePointer('pointerDown', 50)
    firePointer('pointerUp', 51) // 1px — below the range threshold

    expect(mockPostRange).not.toHaveBeenCalled()
    expect(screen.queryByTestId('range-selection-overlay')).not.toBeInTheDocument()
  })
})
