/**
 * Shared jest manual mock for lightweight-charts v5 (Plan 0095 phase 1).
 *
 * v5 ships ESM-only (its package `exports` map has only an `import` condition, no
 * `require`), so jest's CommonJS resolver cannot load the real module. On v4 (which
 * shipped a CJS build) files that value-imported the module resolved fine; the v5
 * bump makes every value import fail to resolve under jsdom/jest. This mock is wired
 * via `moduleNameMapper` in `jest.config.ts` so every suite resolves the module to
 * these stubs.
 *
 * It provides the v5 series-definition sentinels (passed to `chart.addSeries`), the
 * enums used as values, and a `createSeriesMarkers` + `createChart` stub good enough
 * for the lib/hook/view suites that merely need the import to resolve. Component
 * suites that assert on chart interactions keep their own `jest.mock` factory, which
 * overrides this default for their file.
 */

// Series-definition sentinels. Production calls `chart.addSeries(LineSeries, opts)`;
// a chart mock dispatches on the identity of these objects.
export const CandlestickSeries = { seriesType: 'Candlestick' } as const
export const BarSeries = { seriesType: 'Bar' } as const
export const LineSeries = { seriesType: 'Line' } as const
export const AreaSeries = { seriesType: 'Area' } as const
export const HistogramSeries = { seriesType: 'Histogram' } as const
export const BaselineSeries = { seriesType: 'Baseline' } as const

// Enums consumed as values in production code.
export const ColorType = { Solid: 'solid', VerticalGradient: 'gradient' } as const
export const LineStyle = {
  Solid: 0,
  Dotted: 1,
  Dashed: 2,
  LargeDashed: 3,
  SparseDotted: 4,
} as const
export const LineType = { Simple: 0, WithSteps: 1, Curved: 2 } as const
export const CrosshairMode = { Normal: 0, Magnet: 1, Hidden: 2 } as const
export const PriceScaleMode = { Normal: 0, Logarithmic: 1, Percentage: 2, IndexedTo100: 3 } as const
export const TickMarkType = {
  Year: 0,
  Month: 1,
  DayOfMonth: 2,
  Time: 3,
  TimeWithSeconds: 4,
} as const

// v5 markers plugin (replaces the removed `ISeriesApi.setMarkers`).
export const createSeriesMarkers = jest.fn(() => ({
  setMarkers: jest.fn(),
  detach: jest.fn(),
  applyOptions: jest.fn(),
  markers: jest.fn(() => []),
}))

function fakeSeries() {
  return {
    setData: jest.fn(),
    update: jest.fn(),
    applyOptions: jest.fn(),
    priceScale: jest.fn(() => ({ applyOptions: jest.fn() })),
    attachPrimitive: jest.fn(),
    detachPrimitive: jest.fn(),
    createPriceLine: jest.fn(() => ({ applyOptions: jest.fn() })),
    removePriceLine: jest.fn(),
    moveToPane: jest.fn(),
  }
}

function fakePane() {
  return {
    setHeight: jest.fn(),
    getHeight: jest.fn(() => 0),
    moveTo: jest.fn(),
    paneIndex: jest.fn(() => 0),
    setStretchFactor: jest.fn(),
    getStretchFactor: jest.fn(() => 1),
    getSeries: jest.fn(() => []),
  }
}

export const createChart = jest.fn(() => ({
  addSeries: jest.fn(() => fakeSeries()),
  addPane: jest.fn(() => fakePane()),
  panes: jest.fn(() => [fakePane()]),
  removePane: jest.fn(),
  removeSeries: jest.fn(),
  remove: jest.fn(),
  applyOptions: jest.fn(),
  resize: jest.fn(),
  priceScale: jest.fn(() => ({ applyOptions: jest.fn() })),
  timeScale: jest.fn(() => ({
    fitContent: jest.fn(),
    getVisibleLogicalRange: jest.fn(() => null),
    setVisibleLogicalRange: jest.fn(),
    subscribeVisibleLogicalRangeChange: jest.fn(),
    unsubscribeVisibleLogicalRangeChange: jest.fn(),
    subscribeVisibleTimeRangeChange: jest.fn(),
    unsubscribeVisibleTimeRangeChange: jest.fn(),
  })),
  subscribeClick: jest.fn(),
  unsubscribeClick: jest.fn(),
  subscribeCrosshairMove: jest.fn(),
  unsubscribeCrosshairMove: jest.fn(),
}))
