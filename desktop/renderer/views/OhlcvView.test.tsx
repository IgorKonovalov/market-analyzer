/**
 * Plan 0013 phase 4 done-when (close-review M1): the OhlcvView -> Toast failure
 * RENDER path. `useBackfillState`'s error STATE is unit-tested in
 * useBackfillState.test.tsx; this defends the piece that was missing — that the
 * view actually renders a toast (and the header spinner) in response to backfill
 * events, ignores non-matching symbols, and that dismiss + re-show work.
 *
 * `useOhlcvHistory` / `useAnnotationsPoll` are mocked to a stable empty state (bars=[]),
 * so the view renders its empty-state body and never mounts the candlestick
 * chart — keeping lightweight-charts/canvas out of jsdom. The REAL
 * `useBackfillState` + `backfillBus` run, so `notifyBackfill` drives the view
 * exactly as App.tsx's `useEventStream` handlers do in production.
 */
import '@testing-library/jest-dom'
import { act, fireEvent, render, screen, within } from '@testing-library/react'

import { notifyBackfill } from '../handlers/backfillBus'
import type { Timeframe } from '../lib/timeframes'
import { useOhlcvHistory } from '../hooks/useOhlcvHistory'
import { useQuotePoll } from '../hooks/useQuotePoll'
import type { QuoteResponse } from '../types/sidecar/quote-response'
import { OhlcvView, PriceHeader } from './OhlcvView'

jest.mock('../hooks/useOhlcvHistory', () => ({
  useOhlcvHistory: jest.fn(() => ({
    bars: [],
    isLoading: false,
    isRefetching: false,
    error: null,
    historyClampedDays: null,
    refetch: jest.fn(),
    loadOlder: jest.fn(),
    isLoadingOlder: false,
    olderError: null,
    reachedStart: false,
  })),
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
// The PriceHeader mounts useQuotePoll, which would otherwise fire GET /quote on
// mount. Keep it inert here (no quote) and drive it explicitly in the price-
// header tests below (Plan 0047 phase 6).
jest.mock('../hooks/useQuotePoll', () => ({
  useQuotePoll: jest.fn(() => ({ quote: null, error: null })),
}))

const mockUseQuotePoll = useQuotePoll as jest.Mock

const SYMBOL = 'AAPL'
const TF: Timeframe = '1d'

const mockUseOhlcvHistory = useOhlcvHistory as jest.Mock

/** A fresh default hook return (empty buffer, idle paging) per test. */
function baseHook(): ReturnType<typeof useOhlcvHistory> {
  return {
    bars: [],
    isLoading: false,
    isRefetching: false,
    error: null,
    historyClampedDays: null,
    refetch: jest.fn(),
    loadOlder: jest.fn(),
    isLoadingOlder: false,
    olderError: null,
    reachedStart: false,
  }
}

beforeEach(() => {
  mockUseOhlcvHistory.mockReturnValue(baseHook())
  mockUseQuotePoll.mockReturnValue({ quote: null, error: null })
})

const QUOTE: QuoteResponse = {
  symbol: SYMBOL,
  price: 61_335.75,
  change_pct: 2.41,
  currency: 'USD',
  as_of: '2026-06-05T14:30:00Z',
}

function renderView(timeframe: Timeframe = TF): ReturnType<typeof render> {
  return render(
    <OhlcvView
      symbol={SYMBOL}
      timeframe={timeframe}
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

describe('Refresh button (visual feedback)', () => {
  it('clicking Refresh advances the window (onRefresh) AND forces a reload (refetch)', () => {
    const onRefresh = jest.fn()
    const refetch = jest.fn()
    mockUseOhlcvHistory.mockReturnValue({ ...baseHook(), refetch })
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
        onRefresh={onRefresh}
      />,
    )
    fireEvent.click(screen.getByTestId('ohlcv-refresh'))
    expect(onRefresh).toHaveBeenCalledTimes(1)
    expect(refetch).toHaveBeenCalledTimes(1)
  })

  it('shows a "Refreshing…" spinner and disables the button while isRefetching', () => {
    mockUseOhlcvHistory.mockReturnValue({ ...baseHook(), isRefetching: true })
    renderView()
    const button = screen.getByTestId('ohlcv-refresh')
    expect(button).toHaveTextContent('Refreshing…')
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-busy', 'true')
    // Accessible name stays stable so screen readers / the e2e role lookup
    // always find a "Refresh" button regardless of the transient label.
    expect(screen.getByRole('button', { name: 'Refresh' })).toBe(button)
  })

  it('flashes "Updated ✓" after a refresh settles, then reverts to "Refresh"', () => {
    jest.useFakeTimers()
    try {
      mockUseOhlcvHistory.mockReturnValue({ ...baseHook(), isRefetching: true })
      const { rerender } = renderView()
      expect(screen.getByTestId('ohlcv-refresh')).toHaveTextContent('Refreshing…')

      // The refetch settles: isRefetching falls true→false with no error.
      mockUseOhlcvHistory.mockReturnValue({ ...baseHook(), isRefetching: false })
      act(() => {
        rerender(
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
      })
      expect(screen.getByTestId('ohlcv-refresh')).toHaveTextContent('Updated')

      act(() => {
        jest.advanceTimersByTime(1400)
      })
      expect(screen.getByTestId('ohlcv-refresh')).toHaveTextContent('Refresh')
      expect(screen.getByTestId('ohlcv-refresh')).not.toHaveTextContent('Updated')
    } finally {
      jest.useRealTimers()
    }
  })
})

describe('lazy-history affordances (Plan 0030 phase 2)', () => {
  it('renders the loading affordance iff isLoadingOlder', () => {
    mockUseOhlcvHistory.mockReturnValue({ ...baseHook(), isLoadingOlder: true })
    renderView()
    expect(screen.getByTestId('ohlcv-history-loading')).toBeInTheDocument()
  })

  it('does not render the loading affordance when not loading older', () => {
    renderView()
    expect(screen.queryByTestId('ohlcv-history-loading')).not.toBeInTheDocument()
  })

  it('renders the error chip iff olderError, and its retry re-invokes loadOlder', () => {
    const loadOlder = jest.fn()
    mockUseOhlcvHistory.mockReturnValue({
      ...baseHook(),
      olderError: new Error('upstream 502'),
      loadOlder,
    })
    renderView()

    const chip = screen.getByTestId('ohlcv-history-error')
    expect(chip).toBeInTheDocument()
    fireEvent.click(within(chip).getByRole('button', { name: 'Retry' }))
    expect(loadOlder).toHaveBeenCalledTimes(1)
  })

  it('renders neither affordance when reachedStart is true', () => {
    mockUseOhlcvHistory.mockReturnValue({
      ...baseHook(),
      isLoadingOlder: true,
      olderError: new Error('ignored once start reached'),
      reachedStart: true,
    })
    renderView()
    expect(screen.queryByTestId('ohlcv-history-loading')).not.toBeInTheDocument()
    expect(screen.queryByTestId('ohlcv-history-error')).not.toBeInTheDocument()
  })
})

describe('history-clamped notice', () => {
  it('renders the info notice with the cap + timeframe when historyClampedDays is set', () => {
    mockUseOhlcvHistory.mockReturnValue({ ...baseHook(), historyClampedDays: 60 })
    renderView('15m')
    const notice = screen.getByTestId('ohlcv-history-clamped')
    expect(notice).toBeInTheDocument()
    expect(notice).toHaveAttribute('role', 'status')
    expect(notice).toHaveTextContent('showing max available history (~60d) for 15m')
  })

  it('does not render the notice when historyClampedDays is null', () => {
    renderView('15m')
    expect(screen.queryByTestId('ohlcv-history-clamped')).not.toBeInTheDocument()
  })

  it('suppresses the notice while an error is showing (error path wins)', () => {
    mockUseOhlcvHistory.mockReturnValue({
      ...baseHook(),
      error: new Error('boom'),
      historyClampedDays: 60,
    })
    renderView('15m')
    expect(screen.queryByTestId('ohlcv-history-clamped')).not.toBeInTheDocument()
  })
})

describe('live price header (Plan 0047 phase 6)', () => {
  it('shows the polled quote price and day-change %', () => {
    mockUseQuotePoll.mockReturnValue({ quote: QUOTE, error: null })
    renderView()
    expect(screen.getByTestId('price-value')).toHaveTextContent('61,335.75 USD')
    const change = screen.getByTestId('price-change')
    expect(change).toHaveTextContent('+2.41%')
    expect(change).toHaveAttribute('data-direction', 'up')
  })

  it('does not change when the timeframe switches 1h→1d (tracks the quote, not the last bar)', () => {
    mockUseQuotePoll.mockReturnValue({ quote: { ...QUOTE, price: 123.45 }, error: null })
    const { rerender } = renderView('1h')
    const before = screen.getByTestId('price-value').textContent

    rerender(
      <OhlcvView
        symbol={SYMBOL}
        timeframe={'1d'}
        range_start="2026-04-01T00:00:00Z"
        range_end="2026-05-01T00:00:00Z"
        liveHighlights={[]}
        overlays={[]}
        onSymbolChange={() => {}}
        onTimeframeChange={() => {}}
        onRefresh={() => {}}
      />,
    )
    // The price is fed by useQuotePoll (keyed on symbol only), so a timeframe
    // switch leaves it untouched — never re-derived from the OHLCV series.
    expect(screen.getByTestId('price-value').textContent).toBe(before)
    expect(screen.getByTestId('price-value')).toHaveTextContent('123.45 USD')
  })

  it('renders a negative change in the bearish direction', () => {
    mockUseQuotePoll.mockReturnValue({ quote: { ...QUOTE, change_pct: -1.8 }, error: null })
    renderView()
    const change = screen.getByTestId('price-change')
    expect(change).toHaveTextContent('-1.80%')
    expect(change).toHaveAttribute('data-direction', 'down')
  })

  it('degrades to an em dash (no crash, no change badge) when no quote has arrived', () => {
    mockUseQuotePoll.mockReturnValue({ quote: null, error: new Error('quote poll failed') })
    renderView()
    expect(screen.getByTestId('price-value')).toHaveTextContent('—')
    expect(screen.queryByTestId('price-change')).not.toBeInTheDocument()
  })

  it('flags the price as disconnected and dims it when the poll is failing', () => {
    mockUseQuotePoll.mockReturnValue({ quote: QUOTE, error: new Error('quote poll failed') })
    renderView()
    // Last-known price stays visible (not blanked) but is marked stale + flagged.
    expect(screen.getByTestId('price-value')).toHaveTextContent('61,335.75 USD')
    expect(screen.getByTestId('price-value')).toHaveAttribute('data-stale', 'true')
    expect(screen.getByTestId('price-disconnected')).toBeInTheDocument()
  })

  it('shows no disconnected flag while polling succeeds', () => {
    mockUseQuotePoll.mockReturnValue({ quote: QUOTE, error: null })
    renderView()
    expect(screen.getByTestId('price-value')).not.toHaveAttribute('data-stale')
    expect(screen.queryByTestId('price-disconnected')).not.toBeInTheDocument()
  })
})

describe('PriceHeader (unit)', () => {
  it('omits the currency suffix when the quote carries none', () => {
    render(<PriceHeader symbol="BTC-USD" quote={{ ...QUOTE, currency: '' }} />)
    expect(screen.getByTestId('price-value')).toHaveTextContent('61,335.75')
    expect(screen.getByTestId('price-value').textContent).not.toContain('USD')
  })

  it('omits the change badge when change_pct is null', () => {
    render(<PriceHeader symbol="BTC-USD" quote={{ ...QUOTE, change_pct: null }} />)
    expect(screen.queryByTestId('price-change')).not.toBeInTheDocument()
  })

  it('renders the disconnected badge and marks the price stale when disconnected', () => {
    render(<PriceHeader symbol="BTC-USD" quote={QUOTE} disconnected />)
    expect(screen.getByTestId('price-disconnected')).toBeInTheDocument()
    expect(screen.getByTestId('price-value')).toHaveAttribute('data-stale', 'true')
  })

  it('does not mark an em dash stale when disconnected with no quote yet', () => {
    render(<PriceHeader symbol="BTC-USD" quote={null} disconnected />)
    // The badge still informs the user why there is no price…
    expect(screen.getByTestId('price-disconnected')).toBeInTheDocument()
    // …but there is no last-known value to dim, so the dash isn't marked stale.
    expect(screen.getByTestId('price-value')).toHaveTextContent('—')
    expect(screen.getByTestId('price-value')).not.toHaveAttribute('data-stale')
  })
})
