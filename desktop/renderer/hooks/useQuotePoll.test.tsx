/**
 * Plan 0047 phase 6 done-when: useQuotePoll behavior.
 *
 * Defends:
 * - The hook calls `api.getQuote(symbol)` on mount and on every interval tick.
 * - A second stubbed response updates the quote on the next tick (live refresh).
 * - Polling is suspended when the tab is hidden (no offscreen Yahoo hammering).
 * - A failed poll surfaces on `error` WITHOUT clobbering the last-known quote.
 * - The interval is cleared on unmount — no leaked timers.
 */
import '@testing-library/jest-dom'

import { act, renderHook, waitFor } from '@testing-library/react'

import { useQuotePoll } from './useQuotePoll'
import type { QuoteResponse } from '../types/sidecar/quote-response'

const SYMBOL = 'BTC-USD'

function quote(price: number): QuoteResponse {
  return { symbol: SYMBOL, price, change_pct: 1.0, currency: 'USD', as_of: '2026-06-05T14:30:00Z' }
}

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

/** Stub `/quote` to return the given response bodies in sequence (last one
 * sticks). A status >= 400 drives the hook's error branch. */
function setupFetch(responses: Array<{ body: unknown; status?: number }>): { count: () => number } {
  let i = 0
  const seq = responses.length > 0 ? responses : [{ body: quote(1) }]
  global.fetch = jest.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (!url.includes('/quote')) return mockResponse('not mocked', 500)
    const r = seq[Math.min(i, seq.length - 1)]
    i += 1
    return mockResponse(r.body, r.status ?? 200)
  }) as unknown as typeof fetch
  return { count: () => i }
}

function setVisibility(state: 'visible' | 'hidden'): void {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => state,
  })
}

beforeEach(() => {
  jest.useFakeTimers()
  setupWindowApi()
  setVisibility('visible')
})

afterEach(() => {
  jest.useRealTimers()
  jest.restoreAllMocks()
})

it('fetches the quote immediately on mount and hits /quote?symbol=', async () => {
  setupFetch([{ body: quote(61_000) }])

  const { result } = renderHook(() => useQuotePoll({ symbol: SYMBOL, intervalMs: 10_000 }))

  await waitFor(() => expect(result.current.quote?.price).toBe(61_000))
  const calledUrl = (global.fetch as jest.Mock).mock.calls[0][0] as string
  const url = new URL(calledUrl)
  expect(url.pathname).toBe('/quote')
  expect(url.searchParams.get('symbol')).toBe(SYMBOL)
})

it('refreshes the quote on the next poll tick (second stubbed response)', async () => {
  setupFetch([{ body: quote(61_000) }, { body: quote(61_500) }])

  const { result } = renderHook(() => useQuotePoll({ symbol: SYMBOL, intervalMs: 10_000 }))
  await waitFor(() => expect(result.current.quote?.price).toBe(61_000))

  act(() => {
    jest.advanceTimersByTime(10_000)
  })
  await waitFor(() => expect(result.current.quote?.price).toBe(61_500))
})

it('suspends polling while the tab is hidden', async () => {
  const fetchState = setupFetch([{ body: quote(61_000) }])

  renderHook(() => useQuotePoll({ symbol: SYMBOL, intervalMs: 10_000 }))
  await waitFor(() => expect(fetchState.count()).toBeGreaterThanOrEqual(1))
  const before = fetchState.count()

  setVisibility('hidden')
  act(() => {
    jest.advanceTimersByTime(30_000)
  })
  await Promise.resolve()
  expect(fetchState.count()).toBe(before)
})

it('keeps the last-known quote and surfaces error when a poll fails', async () => {
  setupFetch([{ body: quote(61_000) }, { body: 'upstream down', status: 502 }])

  const { result } = renderHook(() => useQuotePoll({ symbol: SYMBOL, intervalMs: 10_000 }))
  await waitFor(() => expect(result.current.quote?.price).toBe(61_000))

  act(() => {
    jest.advanceTimersByTime(10_000)
  })
  await waitFor(() => expect(result.current.error).not.toBeNull())
  // Last-known quote stays on screen rather than blanking.
  expect(result.current.quote?.price).toBe(61_000)
})

it('clears the interval on unmount (no further polls)', async () => {
  const fetchState = setupFetch([{ body: quote(61_000) }])

  const { unmount } = renderHook(() => useQuotePoll({ symbol: SYMBOL, intervalMs: 10_000 }))
  await waitFor(() => expect(fetchState.count()).toBeGreaterThanOrEqual(1))
  const before = fetchState.count()

  unmount()
  act(() => {
    jest.advanceTimersByTime(30_000)
  })
  await Promise.resolve()
  expect(fetchState.count()).toBe(before)
})
