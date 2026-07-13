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
import { OVERLAY_REGISTRY } from '../lib/overlays'
import type { Bar } from '../types/sidecar/bar'
import type { OverlayKind } from '../types/events'

// A kind with no registry entry — the "unsupported" (log-and-skip) probe. rsi/macd
// became real oscillator panes in Plan 0091 phase 9, so a cast synthetic kind now
// stands in for the MVP-unsupported placeholder they used to be.
const UNSUPPORTED = 'unsupported_test_kind' as OverlayKind

// ---------- lightweight-charts mock --------------------------------------- //

interface FakeLineSeries {
  setData: jest.Mock
  applyOptions: jest.Mock
  _opts: { priceScaleId?: string } & Record<string, unknown>
}

interface FakeHistogramSeries {
  setData: jest.Mock
  applyOptions: jest.Mock
  _opts: unknown
}

interface FakeChart {
  // v5: one unified addSeries(SeriesDefinition, opts) (Plan 0095).
  addSeries: jest.Mock
  priceScale: jest.Mock<{ applyOptions: jest.Mock }, [string]>
  removeSeries: jest.Mock<void, [unknown]>
  remove: jest.Mock<void, []>
  applyOptions: jest.Mock<void, [unknown]>
  timeScale: () => {
    fitContent: jest.Mock
    subscribeVisibleLogicalRangeChange: jest.Mock
    unsubscribeVisibleLogicalRangeChange: jest.Mock
  }
  // Plan 0014: the component subscribes to clicks for the bar-click gesture.
  subscribeClick: jest.Mock<void, [unknown]>
  unsubscribeClick: jest.Mock<void, [unknown]>
  // Plan 0047 phase 8: the component subscribes to crosshair moves for the tooltip.
  subscribeCrosshairMove: jest.Mock<void, [unknown]>
  unsubscribeCrosshairMove: jest.Mock<void, [unknown]>
}

let createdLineSeries: FakeLineSeries[] = []
let createdHistogramSeries: FakeHistogramSeries[] = []
let removedLineSeries: FakeLineSeries[] = []
let fakeChart: FakeChart

// The agent OVERLAY line series are the ones added without a `priceScaleId` (they
// ride the default price scale). The always-on Plan 0027 volume/VWAP/OBV lines
// all pin an explicit `priceScaleId`, so this isolates overlays from them.
function overlayLineSeries(): FakeLineSeries[] {
  return createdLineSeries.filter((s) => s._opts.priceScaleId === undefined)
}

jest.mock('lightweight-charts', () => ({
  ...jest.requireActual('../tests/chartMockShared').seriesDefs,
  createSeriesMarkers: jest.requireActual('../tests/chartMockShared').createSeriesMarkers,
  ColorType: { Solid: 'solid' },
  createChart: jest.fn(() => fakeChart),
}))

function buildFakeChart(): FakeChart {
  return {
    ...jest.requireActual('../tests/chartMockShared').paneStubs,
    addSeries: jest.requireActual('../tests/chartMockShared').dispatchAddSeries({
      candle: () => ({
        setData: jest.fn(),
        setMarkers: jest.fn(),
        applyOptions: jest.fn(),
        attachPrimitive: jest.fn(),
        detachPrimitive: jest.fn(),
      }),
      line: (opts: unknown) => {
        const s: FakeLineSeries = {
          setData: jest.fn(),
          applyOptions: jest.fn(),
          _opts: (opts ?? {}) as FakeLineSeries['_opts'],
        }
        createdLineSeries.push(s)
        return s
      },
      histogram: (opts: unknown) => {
        const s: FakeHistogramSeries = { setData: jest.fn(), applyOptions: jest.fn(), _opts: opts }
        createdHistogramSeries.push(s)
        return s
      },
    }),
    priceScale: jest.fn((_id: string) => ({ applyOptions: jest.fn() })),
    removeSeries: jest.fn((s: unknown) => {
      removedLineSeries.push(s as FakeLineSeries)
    }),
    remove: jest.fn(),
    applyOptions: jest.fn(),
    timeScale: () => ({
      fitContent: jest.fn(),
      // Plan 0030: the chart subscribes to the visible range for lazy paging.
      subscribeVisibleLogicalRangeChange: jest.fn(),
      unsubscribeVisibleLogicalRangeChange: jest.fn(),
    }),
    subscribeClick: jest.fn(),
    unsubscribeClick: jest.fn(),
    subscribeCrosshairMove: jest.fn(),
    unsubscribeCrosshairMove: jest.fn(),
  }
}

// The always-on Plan 0027 volume series the chart draws regardless of overlays:
// candlestick + volume histogram + volume MA + VWAP + OBV.
const BASE_KINDS: ReadonlyArray<{ kind: string; period?: number | null }> = [
  { kind: 'candlestick' },
  { kind: 'volume' },
  { kind: 'volume_ma' },
  { kind: 'vwap' },
  { kind: 'obv' },
]
const BASE_COUNT = BASE_KINDS.length

beforeEach(() => {
  createdLineSeries = []
  createdHistogramSeries = []
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
  it('renders the always-on volume block and no overlay line series when no overlays are passed', () => {
    render(<CandlestickChart bars={FIXTURE_BARS} />)
    const hook = window.__test_chart_render__
    expect(hook).toBeDefined()
    expect(hook!.seriesCount).toBe(BASE_COUNT)
    expect(hook!.seriesKinds).toEqual(BASE_KINDS)
    // The volume histogram is drawn; no agent OVERLAY line series exist yet.
    expect(createdHistogramSeries).toHaveLength(1)
    expect(overlayLineSeries()).toHaveLength(0)
  })

  it('renders one EMA line series per supported overlay with the period reflected in the hook', () => {
    render(<CandlestickChart bars={FIXTURE_BARS} overlays={[{ kind: 'ema', period: 20 }]} />)
    const hook = window.__test_chart_render__
    expect(hook).toBeDefined()
    expect(hook!.seriesCount).toBe(BASE_COUNT + 1)
    expect(hook!.seriesKinds).toEqual([...BASE_KINDS, { kind: 'ema', period: 20 }])
    const overlays = overlayLineSeries()
    expect(overlays).toHaveLength(1)
    expect(overlays[0].setData).toHaveBeenCalled()
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
    expect(hook!.seriesCount).toBe(BASE_COUNT + 2) // base + ema20 + ema50
  })

  it('renders a supertrend overlay as TWO masked line series and fires no "unsupported" warning (Plan 0049)', () => {
    const warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => undefined)
    try {
      render(
        <CandlestickChart
          bars={FIXTURE_BARS}
          overlays={[{ kind: 'supertrend', period: 10, multiplier: 3 }]}
        />,
      )
      // Two overlay line series (the up/lower-band + down/upper-band masks), not
      // the generic single series.
      expect(overlayLineSeries()).toHaveLength(2)
      for (const s of overlayLineSeries()) expect(s.setData).toHaveBeenCalled()
      // supertrend is a supported kind — it must NOT log-and-skip.
      expect(warnSpy).not.toHaveBeenCalled()
    } finally {
      warnSpy.mockRestore()
    }
  })

  it('logs a warning and renders no overlay series when the overlay kind is unregistered', () => {
    const warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => undefined)
    try {
      render(<CandlestickChart bars={FIXTURE_BARS} overlays={[{ kind: UNSUPPORTED }]} />)
      const hook = window.__test_chart_render__
      expect(hook).toBeDefined()
      expect(hook!.seriesCount).toBe(BASE_COUNT) // base volume block only
      expect(hook!.seriesKinds).toEqual(BASE_KINDS)
      expect(overlayLineSeries()).toHaveLength(0)
      expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining(UNSUPPORTED))
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
            { kind: UNSUPPORTED },
            { kind: 'sma', period: 50 },
          ]}
        />,
      )
      const hook = window.__test_chart_render__
      expect(hook).toBeDefined()
      expect(hook!.seriesKinds).toEqual([
        ...BASE_KINDS,
        { kind: 'ema', period: 20 },
        { kind: 'sma', period: 50 },
      ])
      expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining(UNSUPPORTED))
    } finally {
      warnSpy.mockRestore()
    }
  })

  it('removes the line series when the overlay is dropped on a re-render', () => {
    const { rerender } = render(
      <CandlestickChart bars={FIXTURE_BARS} overlays={[{ kind: 'ema', period: 20 }]} />,
    )
    expect(window.__test_chart_render__!.seriesCount).toBe(BASE_COUNT + 1)
    const overlaySeries = overlayLineSeries()[0]

    rerender(<CandlestickChart bars={FIXTURE_BARS} overlays={[]} />)
    expect(window.__test_chart_render__!.seriesCount).toBe(BASE_COUNT)
    expect(fakeChart.removeSeries).toHaveBeenCalledTimes(1)
    expect(removedLineSeries[0]).toBe(overlaySeries)
  })

  it('reconciles a newly-registered overlay kind — registry entry is the only seam (Plan 0029)', () => {
    // A synthetic kind is unsupported (logged-and-skipped in the prior test).
    // Adding a single OVERLAY_REGISTRY entry — no other component edit — must make
    // it render as a price-pane line: this is the four-spots-to-one collapse the
    // plan delivers. (A real indicator kind can no longer play this role — they're
    // all registered now.)
    try {
      OVERLAY_REGISTRY[UNSUPPORTED] = { color: '#abcdef', compute: () => [] }
      render(
        <CandlestickChart bars={FIXTURE_BARS} overlays={[{ kind: UNSUPPORTED, period: 14 }]} />,
      )
      const hook = window.__test_chart_render__
      expect(hook!.seriesKinds).toEqual([...BASE_KINDS, { kind: UNSUPPORTED, period: 14 }])
      expect(overlayLineSeries()).toHaveLength(1)
    } finally {
      delete OVERLAY_REGISTRY[UNSUPPORTED]
    }
  })
})
