/**
 * Plan 0024 phase 3 done-when: SymbolPicker autocomplete UI.
 *
 * Defends:
 * - Typing a query renders a dropdown listing results, each showing
 *   symbol + name + exchange + asset type.
 * - Keyboard: ArrowDown highlights a row, Enter selects it -> onSymbolChange.
 * - Mouse: clicking a row selects it -> onSymbolChange with that symbol.
 * - The dropdown is dismissable via Escape.
 *
 * Debounce + stale-response behavior is pinned at the hook level in
 * useSymbolSearch.test.tsx; here we drive the real hook (real fetch mock) so
 * the wiring from keystroke -> dropdown -> selection is covered end to end.
 */
import '@testing-library/jest-dom'

import { useState } from 'react'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

import { SymbolPicker } from './SymbolPicker'
import type { Timeframe } from '../lib/timeframes'

const RESULTS = [
  { symbol: 'BTC-USD', name: 'Bitcoin USD', exchange: 'CCC', quote_type: 'Cryptocurrency' },
  { symbol: 'BTC=F', name: 'Bitcoin Futures', exchange: 'CME', quote_type: 'Futures' },
]

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
    json: async () => body,
  } as unknown as Response
}

function setupWindowApi(): void {
  Object.defineProperty(window, 'api', {
    configurable: true,
    writable: true,
    value: {
      sidecar: {
        getPort: jest.fn().mockResolvedValue({ port: 54321, secretToken: 'renderer-secret' }),
        onStatus: jest.fn(),
      },
    },
  })
}

function setupFetch(body: unknown = RESULTS): void {
  global.fetch = jest.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url.includes('/search')) return mockResponse(body)
    return mockResponse('not mocked', 500)
  }) as unknown as typeof fetch
}

function renderPicker(): { onSymbolChange: jest.Mock; onTimeframeChange: jest.Mock } {
  const onSymbolChange = jest.fn()
  const onTimeframeChange = jest.fn()
  render(
    <SymbolPicker
      symbol="AAPL"
      timeframe="1d"
      onSymbolChange={onSymbolChange}
      onTimeframeChange={onTimeframeChange}
    />,
  )
  return { onSymbolChange, onTimeframeChange }
}

async function typeAndOpen(query: string): Promise<HTMLElement> {
  const input = screen.getByLabelText('Symbol')
  fireEvent.change(input, { target: { value: query } })
  act(() => {
    jest.advanceTimersByTime(300)
  })
  await waitFor(() => expect(screen.getByRole('listbox')).toBeInTheDocument())
  return input
}

beforeEach(() => {
  jest.useFakeTimers()
  setupWindowApi()
  setupFetch()
})

afterEach(() => {
  jest.useRealTimers()
  jest.restoreAllMocks()
})

it('renders a dropdown of results showing symbol, name, exchange and type', async () => {
  renderPicker()
  await typeAndOpen('BTC')

  const listbox = screen.getByRole('listbox')
  const options = within(listbox).getAllByRole('option')
  expect(options).toHaveLength(2)

  expect(options[0]).toHaveTextContent('BTC-USD')
  expect(options[0]).toHaveTextContent('Bitcoin USD')
  expect(options[0]).toHaveTextContent('CCC')
  expect(options[0]).toHaveTextContent('Cryptocurrency')
  expect(options[1]).toHaveTextContent('BTC=F')
})

it('renders a distinct source badge per suggestion and a deep-USD hint on Coinbase -USD rows', async () => {
  // A mixed result set: Coinbase (deep USD), Binance (USDT), Yahoo composite.
  setupFetch([
    { symbol: 'BTC-USD', name: 'Bitcoin USD', exchange: 'Coinbase', quote_type: 'Cryptocurrency' },
    {
      symbol: 'BTCUSDT',
      name: 'Bitcoin Tether',
      exchange: 'Binance',
      quote_type: 'Cryptocurrency',
    },
    { symbol: 'BTC=F', name: 'Bitcoin Futures', exchange: 'CME', quote_type: 'Futures' },
  ])
  renderPicker()
  await typeAndOpen('BTC')

  const options = within(screen.getByRole('listbox')).getAllByRole('option')
  // Each suggestion carries its routed source label, distinctly per row.
  expect(options[0]).toHaveTextContent('Coinbase')
  expect(options[1]).toHaveTextContent('Binance')
  expect(options[2]).toHaveTextContent('CME')

  // Only the Coinbase -USD pair is flagged as the preferred deep-USD crypto
  // suggestion; the Binance USDT pair and the Yahoo future are not.
  expect(within(options[0]).getByText('deep USD')).toBeInTheDocument()
  expect(within(options[1]).queryByText('deep USD')).not.toBeInTheDocument()
  expect(within(options[2]).queryByText('deep USD')).not.toBeInTheDocument()
})

it('selects a row with ArrowDown + Enter, committing the picked symbol', async () => {
  const { onSymbolChange } = renderPicker()
  const input = await typeAndOpen('BTC')

  fireEvent.keyDown(input, { key: 'ArrowDown' })
  fireEvent.keyDown(input, { key: 'Enter' })

  expect(onSymbolChange).toHaveBeenCalledWith('BTC-USD')
  // Picked row commits and the dropdown closes.
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
})

it('selects the second row when ArrowDown is pressed twice', async () => {
  const { onSymbolChange } = renderPicker()
  const input = await typeAndOpen('BTC')

  fireEvent.keyDown(input, { key: 'ArrowDown' })
  fireEvent.keyDown(input, { key: 'ArrowDown' })
  fireEvent.keyDown(input, { key: 'Enter' })

  expect(onSymbolChange).toHaveBeenCalledWith('BTC=F')
})

it('selects a row on mouse click', async () => {
  const { onSymbolChange } = renderPicker()
  await typeAndOpen('BTC')

  const listbox = screen.getByRole('listbox')
  const second = within(listbox).getAllByRole('option')[1]
  // mousedown drives selection (fires before the input blur).
  fireEvent.mouseDown(second)

  expect(onSymbolChange).toHaveBeenCalledWith('BTC=F')
})

it('dismisses the dropdown on Escape', async () => {
  renderPicker()
  const input = await typeAndOpen('BTC')

  fireEvent.keyDown(input, { key: 'Escape' })
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
})

it('syncs the input to an external symbol change (agent chart.show)', () => {
  const onSymbolChange = jest.fn()
  const onTimeframeChange = jest.fn()
  const { rerender } = render(
    <SymbolPicker
      symbol="AAPL"
      timeframe="1d"
      onSymbolChange={onSymbolChange}
      onTimeframeChange={onTimeframeChange}
    />,
  )
  expect(screen.getByLabelText('Symbol')).toHaveValue('AAPL')

  // The committed symbol changes from outside (App's ChartState, driven by an
  // agent chart.show) — the input must follow, not stay on the mount-time value.
  rerender(
    <SymbolPicker
      symbol="BTCUSDT"
      timeframe="1d"
      onSymbolChange={onSymbolChange}
      onTimeframeChange={onTimeframeChange}
    />,
  )
  expect(screen.getByLabelText('Symbol')).toHaveValue('BTCUSDT')
})

it('preserves an in-progress draft across a re-render with an unchanged symbol', () => {
  const onSymbolChange = jest.fn()
  const onTimeframeChange = jest.fn()
  const { rerender } = render(
    <SymbolPicker
      symbol="AAPL"
      timeframe="1d"
      onSymbolChange={onSymbolChange}
      onTimeframeChange={onTimeframeChange}
    />,
  )
  const input = screen.getByLabelText('Symbol')
  fireEvent.change(input, { target: { value: 'TSL' } })
  expect(input).toHaveValue('TSL')

  // An unrelated parent re-render (timeframe changed; committed symbol did NOT)
  // must not clobber the user's half-typed draft.
  rerender(
    <SymbolPicker
      symbol="AAPL"
      timeframe="1h"
      onSymbolChange={onSymbolChange}
      onTimeframeChange={onTimeframeChange}
    />,
  )
  expect(input).toHaveValue('TSL')
})

// ── Segmented timeframe control (Plan 0096 phase 1) ──

/** A stateful wrapper so keyboard select-on-navigate actually walks the group:
 * the picker is controlled, so arrow keys only advance if the parent commits. */
function renderStatefulPicker(initial: Timeframe = '1d'): { onTimeframeChange: jest.Mock } {
  const onTimeframeChange = jest.fn()
  function Wrapper(): JSX.Element {
    const [tf, setTf] = useState<Timeframe>(initial)
    return (
      <SymbolPicker
        symbol="AAPL"
        timeframe={tf}
        onSymbolChange={jest.fn()}
        onTimeframeChange={(value) => {
          onTimeframeChange(value)
          setTf(value)
        }}
      />
    )
  }
  render(<Wrapper />)
  return { onTimeframeChange }
}

it('renders exactly the backend-supported timeframes (15m/1h/4h/1d/1w/1mo) as segments, not 5m/1m', () => {
  renderPicker()
  const group = screen.getByRole('group', { name: 'Timeframe' })
  const labels = within(group)
    .getAllByRole('button')
    .map((b) => b.textContent)
  // Canonical set, cadence-ascending — sourced from lib/timeframes.
  expect(labels).toEqual(['15m', '1h', '4h', '1d', '1w', '1mo'])
  // The previously-offered-but-unfetchable cadences are gone.
  expect(labels).not.toContain('5m')
  expect(labels).not.toContain('1m')
})

it('pins the active segment and puts only it in the tab order (roving tabindex)', () => {
  renderPicker() // active timeframe is 1d
  const active = screen.getByRole('button', { name: '1d' })
  expect(active).toHaveAttribute('aria-pressed', 'true')
  expect(active).toHaveAttribute('aria-current', 'true')
  expect(active).toHaveAttribute('tabindex', '0')

  const inactive = screen.getByRole('button', { name: '1h' })
  expect(inactive).toHaveAttribute('aria-pressed', 'false')
  expect(inactive).not.toHaveAttribute('aria-current')
  expect(inactive).toHaveAttribute('tabindex', '-1')
})

it('selects a timeframe on click, firing onTimeframeChange with the picked value', () => {
  const { onTimeframeChange } = renderStatefulPicker()
  fireEvent.click(screen.getByRole('button', { name: '4h' }))
  expect(onTimeframeChange).toHaveBeenCalledWith('4h')
  // Clicking the already-active segment is a no-op (no redundant callback).
  onTimeframeChange.mockClear()
  fireEvent.click(screen.getByRole('button', { name: '4h' }))
  expect(onTimeframeChange).not.toHaveBeenCalled()
})

it('reaches every timeframe by keyboard: ArrowRight walks (and wraps) the whole set', () => {
  const { onTimeframeChange } = renderStatefulPicker('1d')
  const group = screen.getByRole('group', { name: 'Timeframe' })
  // Six ArrowRights from 1d cycle through the full set and wrap back to 1d.
  for (let i = 0; i < 6; i++) {
    fireEvent.keyDown(group, { key: 'ArrowRight' })
  }
  const reached = onTimeframeChange.mock.calls.map((c) => c[0])
  expect(reached).toEqual(['1w', '1mo', '15m', '1h', '4h', '1d'])
})

it('ArrowLeft moves to the previous segment and Home/End jump to the ends', () => {
  const { onTimeframeChange } = renderStatefulPicker('1d')
  const group = screen.getByRole('group', { name: 'Timeframe' })
  fireEvent.keyDown(group, { key: 'ArrowLeft' })
  expect(onTimeframeChange).toHaveBeenLastCalledWith('4h')
  fireEvent.keyDown(group, { key: 'Home' })
  expect(onTimeframeChange).toHaveBeenLastCalledWith('15m')
  fireEvent.keyDown(group, { key: 'End' })
  expect(onTimeframeChange).toHaveBeenLastCalledWith('1mo')
})
