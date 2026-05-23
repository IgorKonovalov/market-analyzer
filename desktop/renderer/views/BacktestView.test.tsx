/**
 * Plan 0008 phase 5 done-when for `BacktestView`. Four concrete claims:
 *   1. Metrics dl renders one row per `BacktestMetrics` field (7 — total
 *      return, sharpe, max DD, max DD duration, win rate, trade count,
 *      buy-and-hold). Values formatted per `lib/format.ts`.
 *   2. The equity curve series, exposed via `window.__test_backtest_state__
 *      .equityCurve`, contains exactly `len(result.equity_curve)` points.
 *      (The plan literally pointed at `__test_chart_state__.equityCurve`;
 *      we use a separate `__test_backtest_state__` hook so the BacktestView
 *      doesn't pollute the OhlcvView's snapshot — noted in the commit msg.)
 *   3. Trade log: one row per trade, P&L $ = (exit-entry) * (init/entry),
 *      P&L % = (exit-entry) / entry. Asserted via `decorateTrades`.
 *   4. Dangling trade (exit_price === null) renders em-dashes in exit /
 *      P&L columns and an "Open" status badge.
 */
import '@testing-library/jest-dom'
import { render, screen, within } from '@testing-library/react'

import { BacktestView, decorateTrades } from './BacktestView'
import type { BacktestResult } from '../types/sidecar/backtest-result'
import type { EquityPoint } from '../types/sidecar/equity-point'
import type { Trade } from '../types/sidecar/trade'

// ---------- lightweight-charts mock --------------------------------------- //
//
// jsdom has no canvas. Mirror the CandlestickChart overlays test pattern
// — stand in for the API surface BacktestView's inner EquityCurveChart
// touches (`createChart`, `addBaselineSeries`, `setData`, `fitContent`,
// `remove`), and capture setData calls so we can assert the series content.

interface FakeBaselineSeries {
  setData: jest.Mock
  _data: unknown
}

interface FakeChart {
  addBaselineSeries: jest.Mock<FakeBaselineSeries, [unknown]>
  remove: jest.Mock<void, []>
  timeScale: () => { fitContent: jest.Mock }
}

let lastBaselineSeries: FakeBaselineSeries | null = null
let fakeChart: FakeChart

jest.mock('lightweight-charts', () => ({
  ColorType: { Solid: 'solid' },
  createChart: jest.fn(() => fakeChart),
}))

beforeEach(() => {
  lastBaselineSeries = null
  fakeChart = {
    addBaselineSeries: jest.fn((_opts: unknown) => {
      const s: FakeBaselineSeries = {
        _data: null,
        setData: jest.fn(function (this: FakeBaselineSeries, data: unknown) {
          this._data = data
        }),
      }
      lastBaselineSeries = s
      return s
    }),
    remove: jest.fn(),
    timeScale: () => ({ fitContent: jest.fn() }),
  }
  // Clear the window test hook so a residual value from a prior test can't
  // mask a render that fails to populate it.
  delete window.__test_backtest_state__
})

// ---------- fixtures ----------------------------------------------------- //

function equityPoint(ts: string, equity: number): EquityPoint {
  return { ts, equity }
}

function trade(overrides: Partial<Trade> = {}): Trade {
  return {
    entry_bar_index: 0,
    exit_bar_index: 1,
    entry_price: 100,
    exit_price: 110,
    kind: 'long',
    ...overrides,
  }
}

function fixtureResult(overrides: Partial<BacktestResult> = {}): BacktestResult {
  return {
    run_id: 'run-fixture',
    engine_version: '0.1.0',
    strategy_id: 'rsi',
    strategy_version: '0.1.0',
    symbol: 'AAPL',
    timeframe: '1d',
    range_start: '2026-04-20T00:00:00+00:00',
    range_end: '2026-04-22T00:00:00+00:00',
    bars_hash: 'hash-1',
    params: { period: 14 },
    costs: { commission_bps: 0, slippage_bps: 0 },
    initial_capital: 10_000,
    sizing: 'fixed_fraction',
    started_at: '2026-05-20T14:00:00+00:00',
    finished_at: '2026-05-20T14:00:01+00:00',
    trades: [trade()],
    equity_curve: [
      equityPoint('2026-04-20T00:00:00+00:00', 10_000),
      equityPoint('2026-04-21T00:00:00+00:00', 10_500),
      equityPoint('2026-04-22T00:00:00+00:00', 11_000),
    ],
    metrics: {
      total_return: 0.1,
      sharpe: 1.23,
      max_drawdown: -0.05,
      max_drawdown_duration_bars: 2,
      win_rate: 1.0,
      trade_count: 1,
      buy_and_hold_return: 0.08,
    },
    ...overrides,
  }
}

// ---------- decorateTrades unit ------------------------------------------ //

describe('decorateTrades', () => {
  it('computes P&L $ as (exit-entry) * (initial/entry) for closed trades', () => {
    const trades = [trade({ entry_price: 100, exit_price: 110, exit_bar_index: 1 })]
    const equity = [
      equityPoint('2026-04-20T00:00:00+00:00', 10_000),
      equityPoint('2026-04-21T00:00:00+00:00', 11_000),
    ]
    const [row] = decorateTrades(trades, equity, 10_000)
    expect(row.pnlUsd).toBeCloseTo((110 - 100) * (10_000 / 100), 6) // 1000
    expect(row.pnlPct).toBeCloseTo((110 - 100) / 100, 6) // 0.1
    expect(row.isOpen).toBe(false)
    expect(row.entryTs).toBe('2026-04-20T00:00:00+00:00')
    expect(row.exitTs).toBe('2026-04-21T00:00:00+00:00')
  })

  it('marks dangling trades open with null P&L', () => {
    const trades = [trade({ exit_bar_index: null, exit_price: null })]
    const equity = [equityPoint('2026-04-20T00:00:00+00:00', 10_000)]
    const [row] = decorateTrades(trades, equity, 10_000)
    expect(row.isOpen).toBe(true)
    expect(row.pnlUsd).toBeNull()
    expect(row.pnlPct).toBeNull()
    expect(row.exitTs).toBeNull()
  })

  it('handles losing trades with negative P&L', () => {
    const trades = [trade({ entry_price: 100, exit_price: 90 })]
    const equity = [
      equityPoint('2026-04-20T00:00:00+00:00', 10_000),
      equityPoint('2026-04-21T00:00:00+00:00', 9_000),
    ]
    const [row] = decorateTrades(trades, equity, 10_000)
    expect(row.pnlPct).toBeCloseTo(-0.1, 6)
    expect(row.pnlUsd).toBeCloseTo(-1000, 6)
  })
})

// ---------- BacktestView render ------------------------------------------ //

describe('BacktestView render', () => {
  it('renders all seven metric rows with formatted values', () => {
    render(<BacktestView result={fixtureResult()} />)

    // testids are stable per-metric, exposed by the component.
    expect(screen.getByTestId('metric-total-return')).toHaveTextContent('+10.00%')
    expect(screen.getByTestId('metric-sharpe')).toHaveTextContent('+1.23')
    expect(screen.getByTestId('metric-max-drawdown')).toHaveTextContent('-5.00%')
    expect(screen.getByTestId('metric-max-dd-duration')).toHaveTextContent('2 bars')
    expect(screen.getByTestId('metric-win-rate')).toHaveTextContent('+100.00%')
    expect(screen.getByTestId('metric-trade-count')).toHaveTextContent('1')
    expect(screen.getByTestId('metric-buy-and-hold')).toHaveTextContent('+8.00%')
  })

  it('exposes the equity curve series via window.__test_backtest_state__', () => {
    const result = fixtureResult()
    render(<BacktestView result={result} />)

    expect(window.__test_backtest_state__).toBeDefined()
    expect(window.__test_backtest_state__!.equityCurve.length).toBe(result.equity_curve.length)
    expect(window.__test_backtest_state__!.run_id).toBe(result.run_id)
    expect(window.__test_backtest_state__!.initial_capital).toBe(result.initial_capital)

    // The data shape: each point is {time: number, value: number}, time in
    // seconds (lightweight-charts UTCTimestamp).
    const expectedSeconds = Math.floor(new Date(result.equity_curve[0].ts).getTime() / 1000)
    expect(window.__test_backtest_state__!.equityCurve[0].time).toBe(expectedSeconds)
    expect(window.__test_backtest_state__!.equityCurve[0].value).toBe(result.equity_curve[0].equity)

    // The baseline series was created exactly once.
    expect(fakeChart.addBaselineSeries).toHaveBeenCalledTimes(1)
    expect(lastBaselineSeries).not.toBeNull()
    expect(lastBaselineSeries!.setData).toHaveBeenCalledTimes(1)
  })

  it('renders one trade row per Trade with P&L numbers', () => {
    render(<BacktestView result={fixtureResult()} />)
    const table = screen.getByTestId('trades-table')
    const rows = within(table).getAllByRole('row')
    // 1 header + 1 trade
    expect(rows).toHaveLength(2)
    // P&L $ = (110-100) * (10_000 / 100) = $1,000 → +$1,000.00
    expect(table.textContent).toContain('+$1,000.00')
    // P&L % = (110-100) / 100 = 0.1 → +10.00%
    expect(table.textContent).toContain('+10.00%')
  })

  it('renders em-dashes and an Open badge for a dangling trade', () => {
    const dangling = trade({ exit_bar_index: null, exit_price: null })
    render(<BacktestView result={fixtureResult({ trades: [dangling] })} />)

    expect(screen.getByTestId('trade-open-badge')).toBeInTheDocument()
    // Em-dashes for exit columns (3 places: Exit ts, Exit $, P&L $, P&L %)
    const table = screen.getByTestId('trades-table')
    const dashes = (table.textContent ?? '').match(/—/g) ?? []
    expect(dashes.length).toBeGreaterThanOrEqual(3)
  })

  it('renders the back button and fires onBack when clicked', () => {
    const onBack = jest.fn()
    render(<BacktestView result={fixtureResult()} onBack={onBack} />)
    screen.getByTestId('backtest-back').click()
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('omits the back button when onBack is not provided', () => {
    render(<BacktestView result={fixtureResult()} />)
    expect(screen.queryByTestId('backtest-back')).toBeNull()
  })

  it('renders the strategy/symbol/timeframe header and engine version subtitle', () => {
    render(<BacktestView result={fixtureResult()} />)
    expect(screen.getByTestId('backtest-title').textContent).toContain('rsi v0.1.0')
    expect(screen.getByTestId('backtest-title').textContent).toContain('AAPL')
    expect(screen.getByTestId('backtest-title').textContent).toContain('1d')
    expect(screen.getByTestId('backtest-engine-version').textContent).toContain('0.1.0')
  })
})
