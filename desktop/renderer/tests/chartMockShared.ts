/**
 * Shared v5 lightweight-charts mock pieces for the CandlestickChart component
 * suites (Plan 0095 phase 1). Pulled into each suite's
 * `jest.mock('lightweight-charts', …)` factory via `jest.requireActual` so the
 * series-definition sentinels and the markers-plugin stub live in one place.
 *
 * `dispatchAddSeries` collapses the removed v4 `addCandlestickSeries` /
 * `addLineSeries` / `addHistogramSeries` trio into v5's single
 * `chart.addSeries(SeriesDefinition, options)` — routing by the sentinel's
 * `seriesType` to the same fake-series factories the suite already used.
 *
 * `createSeriesMarkers` routes the plugin's `setMarkers` back to the passed
 * series' own `setMarkers` jest.fn, so suites keep asserting on
 * `series.setMarkers(...)` unchanged even though v5 removed `ISeriesApi.setMarkers`.
 */

/** Series-definition sentinels — spread into the mocked module so production's
 * `import { CandlestickSeries } from 'lightweight-charts'` resolves to these. */
export const seriesDefs = {
  CandlestickSeries: { seriesType: 'Candlestick' },
  BarSeries: { seriesType: 'Bar' },
  LineSeries: { seriesType: 'Line' },
  AreaSeries: { seriesType: 'Area' },
  HistogramSeries: { seriesType: 'Histogram' },
  BaselineSeries: { seriesType: 'Baseline' },
}

/** v5 markers plugin stub. Routes `setMarkers` to the passed series' own
 * `setMarkers` jest.fn (kept on the fake series purely as the capture point). */
export const createSeriesMarkers = jest.fn((series?: { setMarkers?: jest.Mock }) => ({
  setMarkers: series?.setMarkers ?? jest.fn(),
  detach: jest.fn(),
  applyOptions: jest.fn(),
  markers: jest.fn(() => []),
}))

/** v5 pane-API stubs for chart mocks (Plan 0095 phase 2): addPane / removePane /
 * panes. `panes()` returns two panes so a registry pane index (1, the OBV pane)
 * resolves for `setHeight`. Spread into a factory's `createChart` return. */
export const paneStubs = {
  addPane: jest.fn(() => ({ setHeight: jest.fn(), getHeight: jest.fn(() => 0) })),
  removePane: jest.fn(),
  panes: jest.fn(() => [
    { setHeight: jest.fn(), getHeight: jest.fn(() => 0) },
    { setHeight: jest.fn(), getHeight: jest.fn(() => 0) },
  ]),
}

/** Build a v5 `addSeries(def, opts)` jest.fn from per-type fake-series factories.
 * `candle`/`histogram` fall back to `line` when a suite only cares about one. */
export function dispatchAddSeries(makers: {
  candle: (o: unknown) => unknown
  line: (o: unknown) => unknown
  histogram?: (o: unknown) => unknown
  bar?: (o: unknown) => unknown
  area?: (o: unknown) => unknown
  baseline?: (o: unknown) => unknown
}) {
  return jest.fn((def: { seriesType?: string }, o: unknown) => {
    let series: unknown
    switch (def?.seriesType) {
      case 'Candlestick':
        series = makers.candle(o)
        break
      case 'Histogram':
        series = (makers.histogram ?? makers.line)(o)
        break
      case 'Bar':
        series = (makers.bar ?? makers.candle)(o)
        break
      case 'Area':
        series = (makers.area ?? makers.line)(o)
        break
      case 'Baseline':
        series = (makers.baseline ?? makers.line)(o)
        break
      case 'Line':
      default:
        series = makers.line(o)
    }
    // Ensure every mocked series can host a primitive: Plan 0091 phase 9 attaches
    // a `DivergencePrimitive` to the OBV line series and each oscillator pane's line
    // series, not just the candle series. Adds no-op stubs when a suite's maker
    // didn't provide them (harmless for suites that don't assert on primitives).
    const s = series as Record<string, unknown>
    if (typeof s.attachPrimitive !== 'function') s.attachPrimitive = jest.fn()
    if (typeof s.detachPrimitive !== 'function') s.detachPrimitive = jest.fn()
    return series
  })
}
