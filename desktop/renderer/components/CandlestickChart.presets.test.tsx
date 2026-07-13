/**
 * Plan 0096 phase 3 done-when (integration): chart presets + persisted visibility.
 *
 * Drives the REAL component with the shared mocked `lightweight-charts` to assert
 * the behaviours only the wired component proves:
 * - A fresh (symbol, timeframe) opens on Clean (OBV row hidden) and the selector
 *   reads "Clean".
 * - Toggling a layer diverges to "Custom" and PERSISTS across a remount (the
 *   papercut fix — the formerly-ephemeral hidden set is now stored).
 * - Applying a built-in draws its overlays and pins its name.
 * - Save-current-as-preset creates a selectable, active preset.
 *
 * Distinct symbols per test keep the module-singleton stores from bleeding.
 */
import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'

import { CandlestickChart } from './CandlestickChart'
import type { Bar } from '../types/sidecar/bar'

interface FakeLine {
  _opts: { color?: string; priceScaleId?: string } & Record<string, unknown>
  setData: jest.Mock
  applyOptions: jest.Mock
}

jest.mock('lightweight-charts', () => ({
  ...jest.requireActual('../tests/chartMockShared').seriesDefs,
  createSeriesMarkers: jest.requireActual('../tests/chartMockShared').createSeriesMarkers,
  ColorType: { Solid: 'solid' },
  createChart: jest.fn(() => ({
    ...jest.requireActual('../tests/chartMockShared').paneStubs,
    addSeries: jest.requireActual('../tests/chartMockShared').dispatchAddSeries({
      candle: () => ({
        setData: jest.fn(),
        attachPrimitive: jest.fn(),
        detachPrimitive: jest.fn(),
        setMarkers: jest.fn(),
        applyOptions: jest.fn(),
        createPriceLine: jest.fn(() => ({ applyOptions: jest.fn() })),
        removePriceLine: jest.fn(),
      }),
      line: (opts: FakeLine['_opts']) => ({
        _opts: opts,
        setData: jest.fn(),
        applyOptions: jest.fn(),
      }),
      histogram: () => ({ setData: jest.fn(), applyOptions: jest.fn() }),
    }),
    priceScale: jest.fn(() => ({ applyOptions: jest.fn() })),
    removeSeries: jest.fn(),
    remove: jest.fn(),
    applyOptions: jest.fn(),
    timeScale: () => ({
      fitContent: jest.fn(),
      applyOptions: jest.fn(),
      getVisibleLogicalRange: jest.fn(() => null),
      setVisibleLogicalRange: jest.fn(),
      subscribeVisibleLogicalRangeChange: jest.fn(),
      unsubscribeVisibleLogicalRangeChange: jest.fn(),
    }),
    subscribeClick: jest.fn(),
    unsubscribeClick: jest.fn(),
    subscribeCrosshairMove: jest.fn(),
    unsubscribeCrosshairMove: jest.fn(),
  })),
}))

const BARS: Bar[] = Array.from({ length: 3 }, (_, i) => ({
  symbol: 'X',
  timeframe: '1d',
  event_ts: `2026-04-1${i + 3}T00:00:00+00:00`,
  open: 100,
  high: 102,
  low: 99,
  close: 101,
  volume: 1_000_000,
  source: 'fixture',
}))

afterEach(() => {
  try {
    window.localStorage.clear()
  } catch {
    /* ignore */
  }
})

it('opens a fresh symbol on Clean — OBV hidden, selector reads Clean', () => {
  render(<CandlestickChart bars={BARS} symbol="CLEAN-USD" timeframe="1d" />)
  expect(screen.getByTestId('legend-toggle:series:obv')).toHaveAttribute('aria-pressed', 'false')
  expect(screen.getByTestId('preset-select')).toHaveValue('Clean')
})

it('a visibility toggle diverges to Custom and persists across a remount', () => {
  const { unmount } = render(<CandlestickChart bars={BARS} symbol="PERSIST-USD" timeframe="1d" />)
  expect(screen.getByTestId('legend-toggle:series:obv')).toHaveAttribute('aria-pressed', 'false')

  // Turn OBV on.
  fireEvent.click(screen.getByTestId('legend-toggle:series:obv'))
  expect(screen.getByTestId('legend-toggle:series:obv')).toHaveAttribute('aria-pressed', 'true')
  // Diverged from the preset → Custom (the custom option, value '').
  expect(screen.getByTestId('preset-select')).toHaveValue('')

  // Remount the same (symbol, timeframe): the toggle survived (the papercut fix).
  unmount()
  render(<CandlestickChart bars={BARS} symbol="PERSIST-USD" timeframe="1d" />)
  expect(screen.getByTestId('legend-toggle:series:obv')).toHaveAttribute('aria-pressed', 'true')
})

it('applying the Trend preset draws its overlays and pins the name', () => {
  render(<CandlestickChart bars={BARS} symbol="TREND-USD" timeframe="1d" />)
  fireEvent.change(screen.getByTestId('preset-select'), { target: { value: 'Trend' } })
  expect(screen.getByTestId('preset-select')).toHaveValue('Trend')
  // Trend seeds ema 20 as a user overlay → its legend row appears.
  expect(screen.getByTestId('legend-row:overlay:ema:20')).toBeInTheDocument()
})

it('save-current-as-preset creates a selectable, active preset', () => {
  render(<CandlestickChart bars={BARS} symbol="SAVE-USD" timeframe="1d" />)
  fireEvent.click(screen.getByTestId('preset-save-toggle'))
  fireEvent.change(screen.getByTestId('preset-name-input'), { target: { value: 'My view' } })
  fireEvent.submit(screen.getByTestId('preset-save-form'))

  expect(screen.getByTestId('preset-select')).toHaveValue('My view')
  const options = within(screen.getByTestId('preset-select'))
    .getAllByRole('option')
    .map((o) => o.textContent)
  expect(options).toContain('My view')
})
