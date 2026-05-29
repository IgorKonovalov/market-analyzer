/**
 * Plan 0007 phase 4.5 done-when: the `overlays` prop on the chart drives
 * the renderer's `__test_chart_render__` hook. Two cases per the plan:
 *   - Supported kind (ema): one entry per overlay with its period reflected.
 *   - Unsupported kind (rsi): no entry, `console.warn` fired, `seriesCount`
 *     reflects the candlestick only.
 *
 * jsdom has no canvas; we mock `lightweight-charts` so the component never
 * touches a real `IChartApi`. The hook is populated from the component's
 * own ref tracking, not from the chart instance, so the mock only needs to
 * stand in for the API surface (`createChart`, `addCandlestickSeries`,
 * `addLineSeries`, `removeSeries`, `remove`, `timeScale`).
 *
 * Strictly co-located with the component under test rather than at the
 * `OhlcvView` level as the plan literally listed — `desktop/tests/views/`
 * is not in jest.config.ts's `roots`, and the rendering claim is owned by
 * `CandlestickChart` regardless of which view composes it. The plan
 * permits "or equivalent" for exactly this kind of layout reconciliation.
 */
import { render } from '@testing-library/react'

import { CandlestickChart } from './CandlestickChart'
import type { Bar } from '../types/sidecar/bar'

// ---------- lightweight-charts mock --------------------------------------- //

interface FakeLineSeries {
  setData: jest.Mock
  applyOptions: jest.Mock
  _opts: unknown
}

interface FakeCandlestickSeries {
  setData: jest.Mock
  setMarkers: jest.Mock
}

interface FakeChart {
  addCandlestickSeries: jest.Mock<FakeCandlestickSeries, []>
  addLineSeries: jest.Mock<FakeLineSeries, [unknown]>
  removeSeries: jest.Mock<void, [unknown]>
  remove: jest.Mock<void, []>
  timeScale: () => { fitContent: jest.Mock }
  // Plan 0014: the component subscribes to clicks for the bar-click gesture.
  subscribeClick: jest.Mock<void, [unknown]>
  unsubscribeClick: jest.Mock<void, [unknown]>
}

let createdLineSeries: FakeLineSeries[] = []
let removedLineSeries: FakeLineSeries[] = []
let fakeChart: FakeChart

jest.mock('lightweight-charts', () => ({
  ColorType: { Solid: 'solid' },
  createChart: jest.fn(() => fakeChart),
}))

function buildFakeChart(): FakeChart {
  return {
    addCandlestickSeries: jest.fn(() => ({
      setData: jest.fn(),
      setMarkers: jest.fn(),
    })),
    addLineSeries: jest.fn((opts: unknown) => {
      const s: FakeLineSeries = {
        setData: jest.fn(),
        applyOptions: jest.fn(),
        _opts: opts,
      }
      createdLineSeries.push(s)
      return s
    }),
    removeSeries: jest.fn((s: unknown) => {
      removedLineSeries.push(s as FakeLineSeries)
    }),
    remove: jest.fn(),
    timeScale: () => ({ fitContent: jest.fn() }),
    subscribeClick: jest.fn(),
    unsubscribeClick: jest.fn(),
  }
}

beforeEach(() => {
  createdLineSeries = []
  removedLineSeries = []
  fakeChart = buildFakeChart()
  delete (window as { __test_chart_render__?: unknown }).__test_chart_render__
})

// ---------- fixtures ----------------------------------------------------- //

function bar(eventTs: string, close: number): Bar {
  return {
    symbol: 'AAPL',
    timeframe: '1d',
    event_ts: eventTs,
    open: close,
    high: close,
    low: close,
    close,
    volume: 1000,
    source: 'test',
  }
}

const FIXTURE_BARS: Bar[] = Array.from({ length: 30 }, (_, i) => {
  const d = new Date('2026-04-01T00:00:00+00:00')
  d.setUTCDate(d.getUTCDate() + i)
  return bar(d.toISOString(), 100 + i)
})

// ---------- specs --------------------------------------------------------- //

describe('CandlestickChart — overlays prop (Plan 0007 phase 4.5)', () => {
  it('renders a candlestick-only chart when no overlays are passed', () => {
    render(<CandlestickChart bars={FIXTURE_BARS} />)
    const hook = window.__test_chart_render__
    expect(hook).toBeDefined()
    expect(hook!.seriesCount).toBe(1)
    expect(hook!.seriesKinds).toEqual([{ kind: 'candlestick' }])
    expect(fakeChart.addLineSeries).not.toHaveBeenCalled()
  })

  it('renders one EMA line series per supported overlay with the period reflected in the hook', () => {
    render(<CandlestickChart bars={FIXTURE_BARS} overlays={[{ kind: 'ema', period: 20 }]} />)
    const hook = window.__test_chart_render__
    expect(hook).toBeDefined()
    expect(hook!.seriesCount).toBe(2)
    expect(hook!.seriesKinds).toEqual([{ kind: 'candlestick' }, { kind: 'ema', period: 20 }])
    expect(fakeChart.addLineSeries).toHaveBeenCalledTimes(1)
    expect(createdLineSeries[0].setData).toHaveBeenCalled()
  })

  it('renders two EMA series when the overlays prop contains two different periods', () => {
    render(
      <CandlestickChart
        bars={FIXTURE_BARS}
        overlays={[
          { kind: 'ema', period: 20 },
          { kind: 'ema', period: 50 },
        ]}
      />,
    )
    const hook = window.__test_chart_render__
    expect(hook).toBeDefined()
    expect(hook!.seriesKinds.filter((s) => s.kind === 'ema')).toHaveLength(2)
    expect(hook!.seriesCount).toBe(3) // candlestick + ema20 + ema50
  })

  it('logs a warning and renders no series when the overlay kind is rsi (MVP-unsupported)', () => {
    const warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => undefined)
    try {
      render(<CandlestickChart bars={FIXTURE_BARS} overlays={[{ kind: 'rsi', period: 14 }]} />)
      const hook = window.__test_chart_render__
      expect(hook).toBeDefined()
      expect(hook!.seriesCount).toBe(1) // candlestick only
      expect(hook!.seriesKinds).toEqual([{ kind: 'candlestick' }])
      expect(fakeChart.addLineSeries).not.toHaveBeenCalled()
      expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining('rsi'))
    } finally {
      warnSpy.mockRestore()
    }
  })

  it('renders supported overlays alongside unsupported ones — logs the skip, draws the rest', () => {
    const warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => undefined)
    try {
      render(
        <CandlestickChart
          bars={FIXTURE_BARS}
          overlays={[
            { kind: 'ema', period: 20 },
            { kind: 'macd', period: 12 },
            { kind: 'sma', period: 50 },
          ]}
        />,
      )
      const hook = window.__test_chart_render__
      expect(hook).toBeDefined()
      expect(hook!.seriesKinds).toEqual([
        { kind: 'candlestick' },
        { kind: 'ema', period: 20 },
        { kind: 'sma', period: 50 },
      ])
      expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining('macd'))
    } finally {
      warnSpy.mockRestore()
    }
  })

  it('removes the line series when the overlay is dropped on a re-render', () => {
    const { rerender } = render(
      <CandlestickChart bars={FIXTURE_BARS} overlays={[{ kind: 'ema', period: 20 }]} />,
    )
    expect(window.__test_chart_render__!.seriesCount).toBe(2)

    rerender(<CandlestickChart bars={FIXTURE_BARS} overlays={[]} />)
    expect(window.__test_chart_render__!.seriesCount).toBe(1)
    expect(fakeChart.removeSeries).toHaveBeenCalledTimes(1)
    expect(removedLineSeries[0]).toBe(createdLineSeries[0])
  })
})
