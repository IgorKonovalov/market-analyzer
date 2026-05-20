/**
 * Plan 0006 phase 6 done-when: useAnnotationsPoll behavior.
 *
 * Defends:
 * - The hook calls `api.getAnnotations` with the active symbol/timeframe/window
 *   on mount and on every interval tick.
 * - Polling is suspended when `document.visibilityState !== 'visible'` so the
 *   renderer doesn't burn CPU offscreen.
 * - The interval is cleared on unmount — no leaked timers.
 * - A failed poll surfaces on `error` without clobbering the prior
 *   annotations list (markers stay on screen).
 */
import '@testing-library/jest-dom'

import { act, renderHook, waitFor } from '@testing-library/react'

import { useAnnotationsPoll } from './useAnnotationsPoll'

interface MockedFetchCall {
  url: string
  init: RequestInit
}

const SYMBOL = 'AAPL'
const TIMEFRAME = '1d'
const START = new Date('2026-04-01T00:00:00Z')
const END = new Date('2026-05-01T00:00:00Z')

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

function setupFetch(body: unknown = []): { calls: MockedFetchCall[] } {
  const calls: MockedFetchCall[] = []
  global.fetch = jest.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = typeof input === 'string' ? input : input.toString()
    calls.push({ url, init })
    if (url.includes('/annotations')) return mockResponse(body)
    return mockResponse('not mocked', 500)
  }) as unknown as typeof fetch
  return { calls }
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

it('fetches annotations immediately on mount with the active params', async () => {
  const { calls } = setupFetch([
    {
      id: 'ann-1',
      symbol: SYMBOL,
      timeframe: TIMEFRAME,
      event_ts: '2026-04-15T00:00:00+00:00',
      kind: 'bullish_marker',
      label: 'hammer',
      agent_id: 'test',
      created_at: '2026-04-15T01:00:00+00:00',
    },
  ])

  const { result } = renderHook(() =>
    useAnnotationsPoll({ symbol: SYMBOL, timeframe: TIMEFRAME, start: START, end: END }),
  )

  // Let the immediate-fire fetch resolve.
  await waitFor(() => expect(result.current.annotations).toHaveLength(1))

  expect(calls).toHaveLength(1)
  const url = new URL(calls[0].url)
  expect(url.pathname).toBe('/annotations')
  expect(url.searchParams.get('symbol')).toBe(SYMBOL)
  expect(url.searchParams.get('timeframe')).toBe(TIMEFRAME)
  expect(url.searchParams.get('start')).toBe(START.toISOString())
  expect(url.searchParams.get('end')).toBe(END.toISOString())
})

it('polls again on every interval tick', async () => {
  const { calls } = setupFetch([])

  renderHook(() =>
    useAnnotationsPoll({
      symbol: SYMBOL,
      timeframe: TIMEFRAME,
      start: START,
      end: END,
      intervalMs: 1000,
    }),
  )

  await waitFor(() => expect(calls.length).toBeGreaterThanOrEqual(1))
  const initialCalls = calls.length

  act(() => {
    jest.advanceTimersByTime(1000)
  })
  await waitFor(() => expect(calls.length).toBeGreaterThanOrEqual(initialCalls + 1))

  act(() => {
    jest.advanceTimersByTime(1000)
  })
  await waitFor(() => expect(calls.length).toBeGreaterThanOrEqual(initialCalls + 2))
})

it('suspends polling when document.visibilityState is hidden', async () => {
  const { calls } = setupFetch([])

  renderHook(() =>
    useAnnotationsPoll({
      symbol: SYMBOL,
      timeframe: TIMEFRAME,
      start: START,
      end: END,
      intervalMs: 1000,
    }),
  )

  await waitFor(() => expect(calls.length).toBeGreaterThanOrEqual(1))
  const beforeHidden = calls.length

  setVisibility('hidden')
  act(() => {
    jest.advanceTimersByTime(3000)
  })
  // Allow any pending microtasks to drain.
  await Promise.resolve()
  expect(calls.length).toBe(beforeHidden)

  // Visibility returns -> polling resumes.
  setVisibility('visible')
  act(() => {
    jest.advanceTimersByTime(1000)
  })
  await waitFor(() => expect(calls.length).toBeGreaterThan(beforeHidden))
})

it('clears the interval on unmount', async () => {
  const { calls } = setupFetch([])

  const { unmount } = renderHook(() =>
    useAnnotationsPoll({
      symbol: SYMBOL,
      timeframe: TIMEFRAME,
      start: START,
      end: END,
      intervalMs: 1000,
    }),
  )

  await waitFor(() => expect(calls.length).toBeGreaterThanOrEqual(1))
  const beforeUnmount = calls.length

  unmount()
  act(() => {
    jest.advanceTimersByTime(5000)
  })
  await Promise.resolve()
  expect(calls.length).toBe(beforeUnmount)
})

it('surfaces error without clobbering prior annotations', async () => {
  // First call returns one annotation; second call errors.
  let callCount = 0
  global.fetch = jest.fn(async () => {
    callCount += 1
    if (callCount === 1) {
      return mockResponse([
        {
          id: 'ann-1',
          symbol: SYMBOL,
          timeframe: TIMEFRAME,
          event_ts: '2026-04-15T00:00:00+00:00',
          kind: 'bullish_marker',
          label: 'first',
          agent_id: 'test',
          created_at: '2026-04-15T01:00:00+00:00',
        },
      ])
    }
    return mockResponse({ detail: 'transient failure' }, 500)
  }) as unknown as typeof fetch

  const { result } = renderHook(() =>
    useAnnotationsPoll({
      symbol: SYMBOL,
      timeframe: TIMEFRAME,
      start: START,
      end: END,
      intervalMs: 1000,
    }),
  )

  await waitFor(() => expect(result.current.annotations).toHaveLength(1))

  act(() => {
    jest.advanceTimersByTime(1000)
  })

  await waitFor(() => expect(result.current.error).not.toBeNull())
  expect(result.current.annotations).toHaveLength(1)
  expect(result.current.annotations[0].label).toBe('first')
})
