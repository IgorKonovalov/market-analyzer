/**
 * Plan 0096 phase 5 (App wiring): the collapsed nav is wired end-to-end and the
 * SSE-driven backtest auto-switch survives the refactor.
 *
 * Stubs the SSE stream (capturing its handlers), the chart + alerts views, and
 * the backtest-result hook so App renders in jsdom without a sidecar.
 */
import '@testing-library/jest-dom'
import { act, fireEvent, render, screen } from '@testing-library/react'

import type { EventStreamHandlers } from './hooks/useEventStream'

let capturedHandlers: EventStreamHandlers = {}
jest.mock('./hooks/useEventStream', () => ({
  useEventStream: (h: EventStreamHandlers) => {
    capturedHandlers = h
    return { state: 'open' }
  },
}))
jest.mock('./views/OhlcvView', () => ({ OhlcvView: () => <div data-testid="ohlcv-stub" /> }))
jest.mock('./views/AlertsView', () => ({ AlertsView: () => <div data-testid="alerts-stub" /> }))
jest.mock('./hooks/useBacktestResult', () => ({
  useBacktestResult: () => ({ status: 'idle' as const }),
}))

import { App } from './App'

beforeEach(() => {
  capturedHandlers = {}
})

it('keeps Chart on the top bar and folds the rest into a collapsed menu', () => {
  render(<App />)
  expect(screen.getByTestId('nav-chart')).toHaveAttribute('aria-current', 'page')
  expect(screen.getByTestId('nav-menu-trigger')).toBeInTheDocument()
  expect(screen.getByTestId('nav-menu-panel')).toHaveAttribute('hidden')
})

it('navigates via the menu — selecting Alerts switches the view and closes the menu', () => {
  render(<App />)
  expect(screen.getByTestId('ohlcv-stub')).toBeInTheDocument()

  fireEvent.click(screen.getByTestId('nav-menu-trigger'))
  fireEvent.click(screen.getByTestId('nav-alerts'))

  expect(screen.getByTestId('alerts-stub')).toBeInTheDocument()
  expect(screen.queryByTestId('ohlcv-stub')).not.toBeInTheDocument()
  expect(screen.getByTestId('nav-menu-panel')).toHaveAttribute('hidden')
})

it('preserves the backtest auto-switch on run.completed(backtest)', () => {
  render(<App />)
  act(() => {
    capturedHandlers.onRunCompleted?.({
      kind: 'backtest',
      run_id: 'run-123',
      artifact_path: 'runs/run-123',
    })
  })
  // View flipped to backtest: the Backtests menu item reads current and the
  // idle backtest panel renders.
  expect(screen.getByTestId('nav-backtests')).toHaveAttribute('aria-current', 'page')
  expect(screen.getByTestId('backtest-idle')).toBeInTheDocument()
})
