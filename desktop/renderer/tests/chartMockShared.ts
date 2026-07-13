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
    switch (def?.seriesType) {
      case 'Candlestick':
        return makers.candle(o)
      case 'Histogram':
        return (makers.histogram ?? makers.line)(o)
      case 'Bar':
        return (makers.bar ?? makers.candle)(o)
      case 'Area':
        return (makers.area ?? makers.line)(o)
      case 'Baseline':
        return (makers.baseline ?? makers.line)(o)
      case 'Line':
      default:
        return makers.line(o)
    }
  })
}
