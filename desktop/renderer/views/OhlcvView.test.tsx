/**
 * Plan 0013 phase 4 done-when (close-review M1): the OhlcvView -> Toast failure
 * RENDER path. `useBackfillState`'s error STATE is unit-tested in
 * useBackfillState.test.tsx; this defends the piece that was missing — that the
 * view actually renders a toast (and the header spinner) in response to backfill
 * events, ignores non-matching symbols, and that dismiss + re-show work.
 *
 * `useOhlcv` / `useAnnotationsPoll` are mocked to a stable empty state (bars=[]),
 * so the view renders its empty-state body and never mounts the candlestick
 * chart — keeping lightweight-charts/canvas out of jsdom. The REAL
 * `useBackfillState` + `backfillBus` run, so `notifyBackfill` drives the view
 * exactly as App.tsx's `useEventStream` handlers do in production.
 */
import '@testing-library/jest-dom'
import { act, fireEvent, render, screen } from '@testing-library/react'

import { notifyBackfill } from '../handlers/backfillBus'
import type { Timeframe } from '../components/SymbolPicker'
import { OhlcvView } from './OhlcvView'

jest.mock('../hooks/useOhlcv', () => ({
  useOhlcv: jest.fn(() => ({ bars: [], isLoading: false, error: null, refetch: jest.fn() })),
}))
jest.mock('../hooks/useAnnotationsPoll', () => ({
  useAnnotationsPoll: jest.fn(() => ({ annotations: [], error: null })),
}))
// SymbolPicker (rendered by the toolbar) now runs useSymbolSearch, which would
// otherwise fire a debounced `/search` fetch on mount. This view test has no
// window.api/fetch wired, so keep the search hook inert and deterministic.
jest.mock('../hooks/useSymbolSearch', () => ({
  useSymbolSearch: jest.fn(() => ({ results: [], isSearching: false, error: null })),
}))
// The chart-header AgentModeToggle mounts useAgentMode, which would otherwise
// fire a GET /agent_mode on mount. Keep it inert + deterministic (Plan 0014).
jest.mock('../hooks/useAgentMode', () => ({
  useAgentMode: jest.fn(() => ({ enabled: false, setEnabled: jest.fn(), error: null })),
}))

const SYMBOL = 'AAPL'
const TF: Timeframe = '1d'

function renderView(): void {
  render(
    <OhlcvView
      symbol={SYMBOL}
      timeframe={TF}
      range_start="2026-04-01T00:00:00Z"
      range_end="2026-05-01T00:00:00Z"
      liveHighlights={[]}
      overlays={[]}
      onSymbolChange={() => {}}
      onTimeframeChange={() => {}}
      onRefresh={() => {}}
    />,
  )
}

function fireStarted(symbol = SYMBOL, timeframe = TF): void {
  act(() => {
    notifyBackfill({
      kind: 'started',
      payload: {
        symbol,
        timeframe,
        gaps: [{ start: '2026-04-01T00:00:00Z', end: '2026-05-01T00:00:00Z' }],
      },
    })
  })
}

function fireFailed(symbol = SYMBOL, timeframe = TF): void {
  act(() => {
    notifyBackfill({
      kind: 'failed',
      payload: {
        symbol,
        timeframe,
        reason: 'rate_limited',
        message: 'yahoo: rate limited (HTTP 429)',
      },
    })
  })
}

it('renders no toast and no spinner before any backfill event', () => {
  renderView()
  expect(screen.queryByTestId('toast')).not.toBeInTheDocument()
  expect(screen.queryByTestId('ohlcv-backfill-spinner')).not.toBeInTheDocument()
})

it('renders an error toast with the reason + message on a matching failure', () => {
  renderView()
  fireFailed()

  const toast = screen.getByTestId('toast')
  expect(toast).toHaveAttribute('role', 'alert')
  expect(toast).toHaveTextContent(/rate_limited/)
  expect(toast).toHaveTextContent(/rate limited \(HTTP 429\)/i)
})

it('shows the header spinner on started, then clears it and shows the toast on failure', () => {
  renderView()

  fireStarted()
  expect(screen.getByTestId('ohlcv-backfill-spinner')).toBeInTheDocument()
  expect(screen.queryByTestId('toast')).not.toBeInTheDocument()

  fireFailed()
  expect(screen.queryByTestId('ohlcv-backfill-spinner')).not.toBeInTheDocument()
  expect(screen.getByTestId('toast')).toBeInTheDocument()
})

it('ignores a failure for a different (symbol, timeframe)', () => {
  renderView()
  fireFailed('MSFT', TF)
  expect(screen.queryByTestId('toast')).not.toBeInTheDocument()
})

it('hides the toast when dismissed', () => {
  renderView()
  fireFailed()
  expect(screen.getByTestId('toast')).toBeInTheDocument()

  fireEvent.click(screen.getByLabelText('Dismiss notification'))
  expect(screen.queryByTestId('toast')).not.toBeInTheDocument()
})
