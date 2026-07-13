/**
 * Plan 0096 phase 4 done-when: the collapsible chart side dock.
 *
 * Defends: it defaults collapsed (chart opens full-width), the collapse state
 * persists in localStorage, and the expanded panel shows contextual symbol
 * details (last / change / OHLC / volume) from the bars the chart already holds.
 */
import '@testing-library/jest-dom'
import { fireEvent, render, screen } from '@testing-library/react'

import { ChartSidePanel } from './ChartSidePanel'
import type { Bar } from '../types/sidecar/bar'

const BARS: Bar[] = [
  {
    symbol: 'BTC-USD',
    timeframe: '1d',
    event_ts: '2026-04-13T00:00:00+00:00',
    open: 100,
    high: 105,
    low: 98,
    close: 100,
    volume: 1000,
    source: 'fixture',
  },
  {
    symbol: 'BTC-USD',
    timeframe: '1d',
    event_ts: '2026-04-14T00:00:00+00:00',
    open: 100,
    high: 112,
    low: 99,
    close: 110,
    volume: 2500,
    source: 'fixture',
  },
]

afterEach(() => {
  try {
    window.localStorage.clear()
  } catch {
    /* ignore */
  }
})

it('defaults collapsed so the chart opens full-width', () => {
  render(<ChartSidePanel symbol="BTC-USD" bars={BARS} />)
  expect(screen.getByTestId('chart-side-panel')).toHaveAttribute('data-collapsed', 'true')
  // No detail rows while collapsed.
  expect(screen.queryByTestId('side-panel-close')).not.toBeInTheDocument()
})

it('expands on toggle, shows OHLC + volume, and persists the state', () => {
  render(<ChartSidePanel symbol="BTC-USD" bars={BARS} />)
  fireEvent.click(screen.getByTestId('side-panel-toggle'))

  const panel = screen.getByTestId('chart-side-panel')
  expect(panel).toHaveAttribute('data-collapsed', 'false')
  expect(window.localStorage.getItem('ma.rightPanelCollapsed')).toBe('false')

  // Latest bar's OHLC + volume, plus the change vs the prior close (100 → 110).
  expect(screen.getByTestId('side-panel-open')).toHaveTextContent('100.00')
  expect(screen.getByTestId('side-panel-high')).toHaveTextContent('112.00')
  expect(screen.getByTestId('side-panel-low')).toHaveTextContent('99.00')
  expect(screen.getByTestId('side-panel-close')).toHaveTextContent('110.00')
  expect(screen.getByTestId('side-panel-volume')).toHaveTextContent('2,500')
  expect(screen.getByTestId('side-panel-change')).toHaveTextContent('+10.00%')

  // Collapse again → persists true.
  fireEvent.click(screen.getByTestId('side-panel-toggle'))
  expect(screen.getByTestId('chart-side-panel')).toHaveAttribute('data-collapsed', 'true')
  expect(window.localStorage.getItem('ma.rightPanelCollapsed')).toBe('true')
})

it('reads the persisted expanded state on a fresh mount', () => {
  window.localStorage.setItem('ma.rightPanelCollapsed', 'false')
  render(<ChartSidePanel symbol="BTC-USD" bars={BARS} />)
  expect(screen.getByTestId('chart-side-panel')).toHaveAttribute('data-collapsed', 'false')
})

it('prefers the live quote price for Last when present', () => {
  window.localStorage.setItem('ma.rightPanelCollapsed', 'false')
  render(
    <ChartSidePanel
      symbol="BTC-USD"
      bars={BARS}
      quote={{
        symbol: 'BTC-USD',
        price: 111.5,
        change_pct: null,
        currency: 'USD',
        as_of: '2026-04-14T12:00:00+00:00',
      }}
    />,
  )
  expect(screen.getByTestId('side-panel-last')).toHaveTextContent('111.50')
})
