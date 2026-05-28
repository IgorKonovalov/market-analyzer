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

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

import { SymbolPicker } from './SymbolPicker'

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
