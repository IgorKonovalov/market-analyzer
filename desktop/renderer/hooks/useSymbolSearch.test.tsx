/**
 * Plan 0024 phase 3 done-when: useSymbolSearch debounce + stale-response guard.
 *
 * Defends:
 * - Debounce is real — typing a multi-char query character-by-character within
 *   the debounce window fires strictly fewer `/search` requests than keystrokes.
 * - A cleared (empty/whitespace) query fires no request and drops results.
 * - Out-of-order responses can't clobber a newer query: an earlier request that
 *   resolves AFTER a later one is ignored; the later query's results win.
 */
import '@testing-library/jest-dom'

import { act, renderHook, waitFor } from '@testing-library/react'

import { useSymbolSearch } from './useSymbolSearch'

const DEBOUNCE = 250

interface Deferred {
  promise: Promise<Response>
  resolve: (body: unknown) => void
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

beforeEach(() => {
  jest.useFakeTimers()
  setupWindowApi()
})

afterEach(() => {
  jest.useRealTimers()
  jest.restoreAllMocks()
})

it('fires far fewer requests than keystrokes (debounce)', async () => {
  let calls = 0
  global.fetch = jest.fn(async () => {
    calls += 1
    return mockResponse([])
  }) as unknown as typeof fetch

  const { rerender } = renderHook(({ q }) => useSymbolSearch(q, DEBOUNCE), {
    initialProps: { q: '' },
  })

  // Type "BTCUSD" one char at a time, each keystroke well within the debounce
  // window so every pending timer is cancelled before it can fire.
  for (const q of ['B', 'BT', 'BTC', 'BTCU', 'BTCUS', 'BTCUSD']) {
    act(() => rerender({ q }))
    act(() => {
      jest.advanceTimersByTime(50)
    })
  }
  expect(calls).toBe(0) // nothing has fired yet — 6 keystrokes, 0 requests

  // Let the final query settle past the debounce.
  act(() => {
    jest.advanceTimersByTime(DEBOUNCE)
  })
  await waitFor(() => expect(calls).toBe(1))
  expect(calls).toBeLessThan(6)
})

it('fires no request for an empty/whitespace query', () => {
  const fetchMock = jest.fn(async () => mockResponse([])) as unknown as typeof fetch
  global.fetch = fetchMock

  const { rerender } = renderHook(({ q }) => useSymbolSearch(q, DEBOUNCE), {
    initialProps: { q: '   ' },
  })
  rerender({ q: '' })

  act(() => {
    jest.advanceTimersByTime(DEBOUNCE * 2)
  })
  expect(fetchMock).not.toHaveBeenCalled()
})

it('ignores an earlier response that resolves after a newer query (stale guard)', async () => {
  const deferreds = new Map<string, Deferred>()
  global.fetch = jest.fn((input: RequestInfo | URL) => {
    const url = new URL(typeof input === 'string' ? input : input.toString())
    const q = url.searchParams.get('q') ?? ''
    let resolveBody!: (body: unknown) => void
    const promise = new Promise<Response>((res) => {
      resolveBody = (body) => res(mockResponse(body))
    })
    deferreds.set(q, { promise, resolve: resolveBody })
    return promise
  }) as unknown as typeof fetch

  const { result, rerender } = renderHook(({ q }) => useSymbolSearch(q, DEBOUNCE), {
    initialProps: { q: 'A' },
  })

  // Fire request for "A".
  act(() => {
    jest.advanceTimersByTime(DEBOUNCE)
  })
  await waitFor(() => expect(deferreds.has('A')).toBe(true))

  // Type ahead to "AB" and fire its request.
  act(() => rerender({ q: 'AB' }))
  act(() => {
    jest.advanceTimersByTime(DEBOUNCE)
  })
  await waitFor(() => expect(deferreds.has('AB')).toBe(true))

  // Resolve the LATER request first...
  await act(async () => {
    deferreds
      .get('AB')!
      .resolve([{ symbol: 'AB-USD', name: 'Later', exchange: '', quote_type: '' }])
  })
  await waitFor(() => expect(result.current.results).toHaveLength(1))
  expect(result.current.results[0].symbol).toBe('AB-USD')

  // ...then resolve the EARLIER request late. It must NOT clobber "AB".
  await act(async () => {
    deferreds
      .get('A')!
      .resolve([{ symbol: 'A-OLD', name: 'Earlier', exchange: '', quote_type: '' }])
  })
  expect(result.current.results).toHaveLength(1)
  expect(result.current.results[0].symbol).toBe('AB-USD')
})
