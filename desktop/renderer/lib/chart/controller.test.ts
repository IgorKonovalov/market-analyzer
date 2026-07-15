/**
 * ChartController unit spec (Plan 0098 phase 1, ADR-0092). Exercises the imperative
 * lightweight-charts wiring WITHOUT rendering the React component — the testability
 * payoff the controller exists for. lightweight-charts is mocked; `createChart`
 * returns a FRESH fake chart per call, so a rebuild is observable as a second
 * `createChart` + the first chart's `remove`. Phase 5 expands this suite; phase 1
 * pins the lifecycle contract the component's candle-type/lazy specs relied on.
 */
import { createChart } from 'lightweight-charts'

import { ChartController } from './controller'
import type { Bar } from '../../types/sidecar/bar'
import type { OverlaySpec } from '../../types/events'

interface FakeSeries {
  kind: string
  setData: jest.Mock
  applyOptions: jest.Mock
  attachPrimitive: jest.Mock
  detachPrimitive: jest.Mock
  priceScale: jest.Mock
  createPriceLine: jest.Mock
  removePriceLine: jest.Mock
  update: jest.Mock
  moveToPane: jest.Mock
}

interface FakeTimeScale {
  fitContent: jest.Mock
  getVisibleLogicalRange: jest.Mock
  setVisibleLogicalRange: jest.Mock
}

interface FakeChart {
  remove: jest.Mock
  timeScaleObj: FakeTimeScale
  [method: string]: unknown
}

let allSeries: FakeSeries[] = []
let charts: FakeChart[] = []

function makeSeries(kind: string): FakeSeries {
  const s: FakeSeries = {
    kind,
    setData: jest.fn(),
    applyOptions: jest.fn(),
    attachPrimitive: jest.fn(),
    detachPrimitive: jest.fn(),
    priceScale: jest.fn(() => ({ applyOptions: jest.fn() })),
    createPriceLine: jest.fn(() => ({ applyOptions: jest.fn() })),
    removePriceLine: jest.fn(),
    update: jest.fn(),
    moveToPane: jest.fn(),
  }
  allSeries.push(s)
  return s
}

function buildChart(): FakeChart {
  const timeScaleObj: FakeTimeScale = {
    fitContent: jest.fn(),
    // A concrete range so the lazy-prepend shift is assertable.
    getVisibleLogicalRange: jest.fn(() => ({ from: 5, to: 15 })),
    setVisibleLogicalRange: jest.fn(),
  }
  const chart: FakeChart = {
    ...jest.requireActual('../../tests/chartMockShared').paneStubs,
    addSeries: jest.requireActual('../../tests/chartMockShared').dispatchAddSeries({
      candle: () => makeSeries('candlestick'),
      bar: () => makeSeries('bar'),
      line: () => makeSeries('line'),
      area: () => makeSeries('area'),
      histogram: () => makeSeries('histogram'),
    }),
    priceScale: jest.fn(() => ({ applyOptions: jest.fn() })),
    removeSeries: jest.fn(),
    remove: jest.fn(),
    applyOptions: jest.fn(),
    timeScaleObj,
    timeScale: () => timeScaleObj,
    subscribeClick: jest.fn(),
    unsubscribeClick: jest.fn(),
    subscribeCrosshairMove: jest.fn(),
    unsubscribeCrosshairMove: jest.fn(),
  }
  charts.push(chart)
  return chart
}

jest.mock('lightweight-charts', () => ({
  ...jest.requireActual('../../tests/chartMockShared').seriesDefs,
  createSeriesMarkers: jest.requireActual('../../tests/chartMockShared').createSeriesMarkers,
  ColorType: { Solid: 'solid' },
  createChart: jest.fn(),
}))

const createChartMock = createChart as unknown as jest.Mock

let container: HTMLDivElement

beforeEach(() => {
  allSeries = []
  charts = []
  createChartMock.mockReset()
  createChartMock.mockImplementation(() => buildChart())
  container = document.createElement('div')
  document.body.appendChild(container)
})

afterEach(() => {
  container.remove()
  jest.restoreAllMocks()
})

function bars(n: number, startDay = 1): Bar[] {
  return Array.from({ length: n }, (_, i) => {
    const d = new Date('2026-04-01T00:00:00+00:00')
    d.setUTCDate(startDay + i)
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
}

/** The main series is the one bearing all five primitives (span + trendline +
 * ichimoku + price-divergence + drawing). The always-on volume/VWAP series bear
 * none, so `> 0` isolates the main series. */
function mainSeries(): FakeSeries[] {
  return allSeries.filter((s) => s.attachPrimitive.mock.calls.length > 0)
}

describe('ChartController — lifecycle (Plan 0098 phase 1)', () => {
  it('mount creates one chart with the main + three always-on series and five primitives on the main series', () => {
    const c = new ChartController()
    c.mount(container, { candleType: 'candles', theme: 'light' })

    expect(createChartMock).toHaveBeenCalledTimes(1)
    // candlestick + volume + volume_ma + vwap.
    expect(allSeries).toHaveLength(4)
    expect(c.chartRef.current).not.toBeNull()
    expect(c.seriesRef.current).not.toBeNull()
    expect(c.volumeSeriesRef.current).not.toBeNull()

    const mains = mainSeries()
    expect(mains).toHaveLength(1)
    expect(mains[0].kind).toBe('candlestick')
    expect(mains[0].attachPrimitive).toHaveBeenCalledTimes(5)
  })

  it('the candle type chooses the main series kind', () => {
    const c = new ChartController()
    c.mount(container, { candleType: 'line', theme: 'light' })
    expect(mainSeries()[0].kind).toBe('line')
  })

  it('setBars pushes data to every always-on series and tracks the bar count', () => {
    const c = new ChartController()
    c.mount(container, { candleType: 'candles', theme: 'light' })
    c.setBars(bars(20))

    expect(c.barCount).toBe(20)
    expect(c.seriesRef.current!.setData).toHaveBeenCalled()
    expect(c.volumeSeriesRef.current!.setData).toHaveBeenCalled()
    expect(c.volumeMaSeriesRef.current!.setData).toHaveBeenCalled()
    expect(c.vwapSeriesRef.current!.setData).toHaveBeenCalled()
  })

  it('a genuine first data load fits the content; a left-edge prepend anchors the viewport instead', () => {
    const c = new ChartController()
    c.mount(container, { candleType: 'candles', theme: 'light' })
    const chart = charts[0]

    // First load fits.
    c.setBars(bars(20, 10))
    expect(chart.timeScaleObj.fitContent).toHaveBeenCalledTimes(1)
    expect(chart.timeScaleObj.setVisibleLogicalRange).not.toHaveBeenCalled()

    // Prepend three older bars (earlier start day): the viewport shifts right by 3,
    // it does NOT refit.
    c.setBars(bars(23, 7))
    expect(chart.timeScaleObj.fitContent).toHaveBeenCalledTimes(1)
    expect(chart.timeScaleObj.setVisibleLogicalRange).toHaveBeenCalledWith({ from: 8, to: 18 })
  })

  it('dispose removes the chart exactly once and nulls its handles (no leaked context)', () => {
    const c = new ChartController()
    c.mount(container, { candleType: 'candles', theme: 'light' })
    const chart = charts[0]
    c.setBars(bars(5))

    c.dispose()

    expect(chart.remove).toHaveBeenCalledTimes(1)
    expect(c.chartRef.current).toBeNull()
    expect(c.seriesRef.current).toBeNull()
    expect(c.paneRegistryRef.current).toBeNull()
    expect(c.spanPrimitiveRef.current).toBeNull()
    expect(c.barCount).toBe(0)
  })

  it('mount → dispose → mount rebuilds on a fresh chart and disposes the first (StrictMode-safe)', () => {
    const c = new ChartController()
    c.mount(container, { candleType: 'candles', theme: 'light' })
    const first = charts[0]

    c.dispose()
    c.mount(container, { candleType: 'line', theme: 'light' })

    expect(createChartMock).toHaveBeenCalledTimes(2)
    expect(first.remove).toHaveBeenCalledTimes(1)
    // The fresh main series is a line series and carries its own five primitives.
    const lineMains = mainSeries().filter((s) => s.kind === 'line')
    expect(lineMains).toHaveLength(1)
    expect(lineMains[0].attachPrimitive).toHaveBeenCalledTimes(5)
  })
})

describe('ChartController — series creation across candle render modes (Plan 0098 phase 5)', () => {
  it.each([
    ['candles', 'candlestick'],
    ['bars', 'bar'],
    ['line', 'line'],
    ['area', 'area'],
  ] as const)('mounts a %s chart with a %s main series', (candleType, expectedKind) => {
    const c = new ChartController()
    c.mount(container, { candleType, theme: 'light' })
    const mains = mainSeries()
    expect(mains).toHaveLength(1)
    expect(mains[0].kind).toBe(expectedKind)
    expect(mains[0].attachPrimitive).toHaveBeenCalledTimes(5)
  })
})

const EMA: OverlaySpec = { kind: 'ema', period: 20 } as OverlaySpec
const RSI: OverlaySpec = { kind: 'rsi' } as OverlaySpec

describe('ChartController — reconcilers through the facade (Plan 0098 phase 5)', () => {
  it('setOverlays adds, reuses, then removes an overlay series without a remount', () => {
    const c = new ChartController()
    c.mount(container, { candleType: 'candles', theme: 'light' })
    c.setBars(bars(30))

    c.setOverlays({ bars: bars(30), overlays: [EMA], hidden: new Set(), theme: 'light' })
    expect(c.overlaySeriesRef.current.size).toBe(1)
    const entry = c.overlaySeriesRef.current.values().next().value as unknown as {
      series: { setData: jest.Mock }
    }
    const afterAdd = entry.series.setData.mock.calls.length

    // Reuse: same overlay again keeps the SAME series (size 1) and recomputes data.
    c.setOverlays({ bars: bars(30), overlays: [EMA], hidden: new Set(), theme: 'light' })
    expect(c.overlaySeriesRef.current.size).toBe(1)
    expect(c.overlaySeriesRef.current.values().next().value).toBe(entry)
    expect(entry.series.setData.mock.calls.length).toBeGreaterThan(afterAdd)

    // Remove.
    c.setOverlays({ bars: bars(30), overlays: [], hidden: new Set(), theme: 'light' })
    expect(c.overlaySeriesRef.current.size).toBe(0)
    // No remount happened through any of this.
    expect(createChartMock).toHaveBeenCalledTimes(1)
  })

  it('setOscillators creates, reuses, then tears down an oscillator pane', () => {
    const c = new ChartController()
    c.mount(container, { candleType: 'candles', theme: 'light' })
    c.setBars(bars(30))
    const empty = new Set<never>()

    c.setOscillators({ bars: bars(30), overlays: [RSI], hidden: new Set(), requiredKinds: empty })
    expect(c.oscillatorPanesRef.current.size).toBe(1)
    const paneEntry = c.oscillatorPanesRef.current.values().next().value

    // Reuse: the SAME pane entry survives a second reconcile.
    c.setOscillators({ bars: bars(30), overlays: [RSI], hidden: new Set(), requiredKinds: empty })
    expect(c.oscillatorPanesRef.current.size).toBe(1)
    expect(c.oscillatorPanesRef.current.values().next().value).toBe(paneEntry)

    // Teardown.
    c.setOscillators({ bars: bars(30), overlays: [], hidden: new Set(), requiredKinds: empty })
    expect(c.oscillatorPanesRef.current.size).toBe(0)
  })

  it('restyle recolours in place (no remount, chart identity stable)', () => {
    const c = new ChartController()
    c.mount(container, { candleType: 'candles', theme: 'light' })
    const chart = charts[0]
    const main = c.seriesRef.current as unknown as { applyOptions: jest.Mock }
    main.applyOptions.mockClear()

    c.restyle('dark')

    expect(createChartMock).toHaveBeenCalledTimes(1) // no remount
    expect((chart as unknown as { applyOptions: jest.Mock }).applyOptions).toHaveBeenCalled()
    expect(main.applyOptions).toHaveBeenCalled()
  })

  it('dispose clears the overlay + oscillator reconciler maps', () => {
    const c = new ChartController()
    c.mount(container, { candleType: 'candles', theme: 'light' })
    c.setBars(bars(30))
    c.setOverlays({ bars: bars(30), overlays: [EMA], hidden: new Set(), theme: 'light' })
    c.setOscillators({
      bars: bars(30),
      overlays: [RSI],
      hidden: new Set(),
      requiredKinds: new Set<never>(),
    })
    expect(c.overlaySeriesRef.current.size).toBe(1)
    expect(c.oscillatorPanesRef.current.size).toBe(1)

    c.dispose()

    expect(c.overlaySeriesRef.current.size).toBe(0)
    expect(c.oscillatorPanesRef.current.size).toBe(0)
    expect(c.seriesRef.current).toBeNull()
  })
})

describe('ChartController — forming bar (setQuote)', () => {
  it('updates the current bar when the quote falls within its period', () => {
    const c = new ChartController()
    c.mount(container, { candleType: 'candles', theme: 'light' })
    const b = bars(5) // last bar starts 2026-04-05T00:00:00Z
    c.setBars(b)
    const series = c.seriesRef.current as unknown as { update: jest.Mock }
    series.update.mockClear()
    c.setQuote({ price: 200, as_of: '2026-04-05T06:00:00+00:00' } as unknown as never, b, '1d')
    expect(series.update).toHaveBeenCalledTimes(1)
  })

  it('leaves every bar when the quote predates the current bar', () => {
    const c = new ChartController()
    c.mount(container, { candleType: 'candles', theme: 'light' })
    const b = bars(5)
    c.setBars(b)
    const series = c.seriesRef.current as unknown as { update: jest.Mock }
    series.update.mockClear()
    c.setQuote({ price: 200, as_of: '2026-04-01T00:00:00+00:00' } as unknown as never, b, '1d')
    expect(series.update).not.toHaveBeenCalled()
  })
})
