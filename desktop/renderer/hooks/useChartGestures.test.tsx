/**
 * Plan 0029 phase 1 done-when: the pointer-gesture FSM, asserted directly on
 * the hook (the component-level matrix still lives in
 * `CandlestickChart.gestures.test.tsx`). Defends (forwarding is unconditional
 * per ADR-0101 — no agent-mode gate):
 *   - a real drag in range mode emits exactly one `postRangeSelected` with the
 *     correct [from, to];
 *   - a click without a drag emits `postBarClicked`;
 *   - Escape mid-drag cancels with no POST;
 *   - missing symbol/timeframe emits nothing for either gesture.
 *
 * The hook owns no chart — it receives the chart/series refs. The harness wires
 * a real container div plus a fake `IChartApi`/series (jsdom has no canvas), so
 * the FSM (pointer math, click-vs-drag, suppression, Escape) is exercised
 * without a real lightweight-charts instance. `../api/uiEvents` is mocked so the
 * assertions land on the POST helpers, not the fetch transport.
 */
import '@testing-library/jest-dom'

import { useRef } from 'react'
import { act, createEvent, fireEvent, render, screen } from '@testing-library/react'
import type { IChartApi, ISeriesApi, MouseEventParams } from 'lightweight-charts'

import { useChartGestures } from './useChartGestures'
import { postBarClicked, postRangeSelected } from '../api/uiEvents'
import type { Bar } from '../types/sidecar/bar'

jest.mock('../api/uiEvents', () => ({
  postRangeSelected: jest.fn().mockResolvedValue(undefined),
  postBarClicked: jest.fn().mockResolvedValue(undefined),
}))

const mockPostRange = postRangeSelected as jest.Mock
const mockPostBar = postBarClicked as jest.Mock

// ---------- fake chart/series (the hook only touches this surface) -------- //

let clickHandler: ((param: MouseEventParams) => void) | null = null
let coordinateToTime: (x: number) => number | null = (x) => 1_714_000_000 + x
let fakeSeries: ISeriesApi<'Candlestick'>
let fakeChart: IChartApi

function buildFakeChart(): IChartApi {
  return {
    subscribeClick: jest.fn((handler: (param: MouseEventParams) => void) => {
      clickHandler = handler
    }),
    unsubscribeClick: jest.fn(() => {
      clickHandler = null
    }),
    applyOptions: jest.fn(),
    timeScale: () => ({ coordinateToTime: (x: number) => coordinateToTime(x) }),
  } as unknown as IChartApi
}

beforeEach(() => {
  jest.clearAllMocks()
  clickHandler = null
  coordinateToTime = (x) => 1_714_000_000 + x
  fakeSeries = {} as ISeriesApi<'Candlestick'>
  fakeChart = buildFakeChart()
})

// ---------- harness ------------------------------------------------------- //

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

function Harness({ symbol, timeframe }: HarnessProps): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(fakeChart)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(fakeSeries)
  const { selectRangeMode, toggleSelectRange, selection, rangeLabel, clickedBarTs } =
    useChartGestures(containerRef, chartRef, seriesRef, {
      symbol,
      timeframe,
      bars: FIXTURE_BARS,
    })
  return (
    <div>
      <button data-testid="toggle" aria-pressed={selectRangeMode} onClick={toggleSelectRange}>
        toggle
      </button>
      <div ref={containerRef} data-testid="container" />
      {selection && <span data-testid="selection" />}
      {rangeLabel && <span data-testid="range-label" />}
      {clickedBarTs && <span data-testid="clicked" />}
    </div>
  )
}

interface HarnessProps {
  symbol?: string
  timeframe?: string
}

// Explicit-undefined must survive (the no-symbol guard test), so default via
// `in`-checks rather than destructuring defaults, which an explicit undefined
// would re-trigger.
function renderHarness(props: HarnessProps = {}): void {
  render(
    <Harness
      symbol={'symbol' in props ? props.symbol : 'AAPL'}
      timeframe={'timeframe' in props ? props.timeframe : '1d'}
    />,
  )
}

function enterRangeMode(): void {
  fireEvent.click(screen.getByTestId('toggle'))
}

// jsdom's PointerEvent drops clientX from the init dict (unlike MouseEvent), so
// force it onto the instance before dispatching — mirrors the component spec.
function firePointer(type: 'pointerDown' | 'pointerMove' | 'pointerUp', clientX: number): void {
  const container = screen.getByTestId('container')
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
  // Invoked directly (not via fireEvent), so wrap in act to flush the
  // clickedBarTs state update into the DOM.
  act(() => {
    clickHandler!({
      time: timeSeconds,
      seriesData: new Map([
        [fakeSeries, { open: ohlc.o, high: ohlc.h, low: ohlc.l, close: ohlc.c }],
      ]),
    } as unknown as MouseEventParams)
  })
}

// ---------- specs --------------------------------------------------------- //

describe('useChartGestures FSM (Plan 0029 phase 1)', () => {
  it('subscribes to clicks on mount once the chart ref is populated', () => {
    renderHarness()
    expect(fakeChart.subscribeClick).toHaveBeenCalledTimes(1)
    expect(clickHandler).not.toBeNull()
  })

  it('range mode: a drag emits one postRangeSelected with the dragged bar times', () => {
    renderHarness()
    enterRangeMode()

    dragChart(40, 120)

    expect(mockPostRange).toHaveBeenCalledTimes(1)
    expect(mockPostRange).toHaveBeenCalledWith({
      symbol: 'AAPL',
      timeframe: '1d',
      range_start: new Date((1_714_000_000 + 40) * 1000).toISOString(),
      range_end: new Date((1_714_000_000 + 120) * 1000).toISOString(),
    })
  })

  it('normalises a right-to-left drag (start <= end)', () => {
    renderHarness()
    enterRangeMode()

    dragChart(120, 40)

    expect(mockPostRange).toHaveBeenCalledWith({
      symbol: 'AAPL',
      timeframe: '1d',
      range_start: new Date((1_714_000_000 + 40) * 1000).toISOString(),
      range_end: new Date((1_714_000_000 + 120) * 1000).toISOString(),
    })
  })

  it('a click without a drag emits postBarClicked with the bar OHLC — no mode precondition', () => {
    renderHarness()

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
    expect(screen.queryByTestId('clicked')).toBeInTheDocument()
  })

  it('a real range drag suppresses the trailing bar-click', () => {
    renderHarness()
    enterRangeMode()

    dragChart(40, 120) // pointerup sets the suppress flag
    clickBar(1_714_000_080, { o: 1, h: 2, l: 0, c: 1.5 }) // the trailing click

    expect(mockPostRange).toHaveBeenCalledTimes(1)
    expect(mockPostBar).not.toHaveBeenCalled()
  })

  it('Escape mid-drag cancels the selection with no POST', () => {
    renderHarness()
    enterRangeMode()

    firePointer('pointerDown', 40)
    fireEvent.keyDown(window, { key: 'Escape' })
    firePointer('pointerUp', 120)

    expect(mockPostRange).not.toHaveBeenCalled()
    expect(screen.queryByTestId('selection')).not.toBeInTheDocument()
    // Escape also leaves range mode.
    expect(screen.getByTestId('toggle')).toHaveAttribute('aria-pressed', 'false')
  })

  it('keeps the selection + range label after a real drag releases', () => {
    renderHarness()
    enterRangeMode()

    firePointer('pointerDown', 40)
    firePointer('pointerMove', 120)
    expect(screen.queryByTestId('selection')).toBeInTheDocument()

    firePointer('pointerUp', 120)
    expect(screen.queryByTestId('selection')).toBeInTheDocument()
    expect(screen.queryByTestId('range-label')).toBeInTheDocument()
  })

  it('discards a click-sized drag (no selection, no POST)', () => {
    renderHarness()
    enterRangeMode()

    firePointer('pointerDown', 50)
    firePointer('pointerUp', 51) // 1px — below MIN_RANGE_SELECT_PX

    expect(mockPostRange).not.toHaveBeenCalled()
    expect(screen.queryByTestId('selection')).not.toBeInTheDocument()
  })

  it('missing symbol/timeframe: neither a drag nor a click POSTs', () => {
    renderHarness({ symbol: undefined, timeframe: undefined })
    enterRangeMode()

    dragChart(40, 120)
    clickBar(1_714_000_500, { o: 1, h: 2, l: 0, c: 1.5 })

    expect(mockPostRange).not.toHaveBeenCalled()
    expect(mockPostBar).not.toHaveBeenCalled()
  })
})
