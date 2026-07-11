/**
 * Plan 0030 phase 1 done-when: the `useOhlcvHistory` buffer brain. `api.getOhlcv`
 * is mocked; `ApiError` is the real class so the hook's `instanceof` + `.status`
 * 422-backoff branch is exercised faithfully.
 *
 * Defends: initial-load parity with `useOhlcv`, prepend window math, dedup at
 * the join, the in-flight re-entrancy guard, reached-start latching (empty AND
 * all-duplicate), 422 halve-and-retry, non-422 error surfacing, and the three
 * re-anchor cases (symbol reset / overlapping edge-fill / disjoint reset).
 */
import '@testing-library/jest-dom'

import { act, renderHook, waitFor } from '@testing-library/react'

import { ApiError, api } from '../api/client'
import { useOhlcvHistory } from './useOhlcvHistory'
import type { Bar } from '../types/sidecar/bar'

jest.mock('../api/client', () => {
  class MockApiError extends Error {
    readonly status: number
    readonly body: string
    constructor(status: number, body: string) {
      super(`sidecar ${status}: ${body}`)
      this.name = 'ApiError'
      this.status = status
      this.body = body
    }
  }
  return {
    ApiError: MockApiError,
    api: { getOhlcv: jest.fn() },
  }
})

const getOhlcv = api.getOhlcv as jest.Mock

const SYMBOL = 'AAPL'
const TF = '1d'
const DAY_MS = 24 * 60 * 60 * 1000
const START = new Date('2026-04-01T00:00:00.000Z')
const END = new Date('2026-05-01T00:00:00.000Z')
const WINDOW_SPAN_MS = END.getTime() - START.getTime()

function bar(eventTs: string, close = 100): Bar {
  return {
    symbol: SYMBOL,
    timeframe: TF,
    event_ts: eventTs,
    open: close,
    high: close + 1,
    low: close - 1,
    close,
    volume: 1000,
    source: 'test',
  }
}

/** Daily bars at [from, from+count) days, ascending. */
function dailyBars(from: Date, count: number): Bar[] {
  return Array.from({ length: count }, (_, i) =>
    bar(new Date(from.getTime() + i * DAY_MS).toISOString(), 100 + i),
  )
}

function renderHistory(
  props: { symbol: string; timeframe: string; start: Date; end: Date } = {
    symbol: SYMBOL,
    timeframe: TF,
    start: START,
    end: END,
  },
) {
  return renderHook((p) => useOhlcvHistory(p), { initialProps: props })
}

/** A promise we can resolve/reject by hand, to pin in-flight behavior. */
function deferred<T>(): {
  promise: Promise<T>
  resolve: (v: T) => void
  reject: (e: unknown) => void
} {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  getOhlcv.mockReset()
})

it('initial load: one getOhlcv for [start, end]; bars sorted ascending; isLoading true→false; reachedStart false', async () => {
  // Returned out of order to prove the hook sorts.
  const unsorted = [...dailyBars(START, 5)].reverse()
  getOhlcv.mockResolvedValueOnce(unsorted)

  const { result } = renderHistory()

  expect(result.current.isLoading).toBe(true)
  await waitFor(() => expect(result.current.isLoading).toBe(false))

  expect(getOhlcv).toHaveBeenCalledTimes(1)
  expect(getOhlcv.mock.calls[0][0]).toMatchObject({ symbol: SYMBOL, timeframe: TF })
  expect(getOhlcv.mock.calls[0][0].start.toISOString()).toBe(START.toISOString())
  expect(getOhlcv.mock.calls[0][0].end.toISOString()).toBe(END.toISOString())

  const tsAsc = result.current.bars!.map((b) => b.event_ts)
  expect(tsAsc).toEqual([...tsAsc].sort())
  expect(result.current.bars).toHaveLength(5)
  expect(result.current.reachedStart).toBe(false)
})

it('prepend: loadOlder fetches [T0 - span, T0] and prepends; isLoadingOlder toggles', async () => {
  const initial = dailyBars(START, 5) // earliest bar T0 = START
  const older = dailyBars(new Date(START.getTime() - 5 * DAY_MS), 5)
  getOhlcv.mockResolvedValueOnce(initial)
  const olderDef = deferred<Bar[]>()
  getOhlcv.mockReturnValueOnce(olderDef.promise)

  const { result } = renderHistory()
  await waitFor(() => expect(result.current.bars).toHaveLength(5))

  act(() => result.current.loadOlder())
  expect(result.current.isLoadingOlder).toBe(true)

  // The older window: end = T0 (earliest bar), start = T0 - chunkSpan.
  const call = getOhlcv.mock.calls[1][0]
  expect(call.end.toISOString()).toBe(START.toISOString())
  expect(call.start.toISOString()).toBe(new Date(START.getTime() - WINDOW_SPAN_MS).toISOString())

  await act(async () => {
    olderDef.resolve(older)
    await olderDef.promise
  })

  await waitFor(() => expect(result.current.isLoadingOlder).toBe(false))
  expect(result.current.bars).toHaveLength(10)
  const tsAsc = result.current.bars!.map((b) => b.event_ts)
  expect(tsAsc).toEqual([...tsAsc].sort())
  expect(new Set(tsAsc).size).toBe(10) // no duplicates
})

it('dedup at the join: an older bar whose event_ts already exists appears once', async () => {
  const initial = dailyBars(START, 3) // T0 = START
  // Older chunk overlaps: includes a bar at START (duplicate) plus two new ones.
  const older = dailyBars(new Date(START.getTime() - 2 * DAY_MS), 3) // [-2d, -1d, START]
  getOhlcv.mockResolvedValueOnce(initial)
  getOhlcv.mockResolvedValueOnce(older)

  const { result } = renderHistory()
  await waitFor(() => expect(result.current.bars).toHaveLength(3))

  await act(async () => {
    result.current.loadOlder()
    await Promise.resolve()
  })
  await waitFor(() => expect(result.current.isLoadingOlder).toBe(false))

  const dupTs = START.toISOString()
  expect(result.current.bars!.filter((b) => b.event_ts === dupTs)).toHaveLength(1)
  expect(result.current.bars).toHaveLength(5) // 3 + 2 genuinely new
})

it('re-entrancy guard: a second loadOlder while the first is in flight issues no second fetch', async () => {
  getOhlcv.mockResolvedValueOnce(dailyBars(START, 3))
  getOhlcv.mockReturnValueOnce(deferred<Bar[]>().promise) // never resolves

  const { result } = renderHistory()
  await waitFor(() => expect(result.current.bars).toHaveLength(3))

  act(() => {
    result.current.loadOlder()
    result.current.loadOlder()
  })

  // 1 initial + exactly 1 older fetch (the second loadOlder was a no-op).
  expect(getOhlcv).toHaveBeenCalledTimes(2)
})

it('reached-start (empty): an empty older fetch latches reachedStart and stops further fetches', async () => {
  getOhlcv.mockResolvedValueOnce(dailyBars(START, 3))
  getOhlcv.mockResolvedValueOnce([])

  const { result } = renderHistory()
  await waitFor(() => expect(result.current.bars).toHaveLength(3))

  await act(async () => {
    result.current.loadOlder()
    await Promise.resolve()
  })
  await waitFor(() => expect(result.current.reachedStart).toBe(true))

  const callsAfterReached = getOhlcv.mock.calls.length
  act(() => result.current.loadOlder())
  expect(getOhlcv).toHaveBeenCalledTimes(callsAfterReached) // no new fetch
})

it('reached-start (all-duplicate): an older fetch of only-buffered bars latches reachedStart', async () => {
  const initial = dailyBars(START, 3)
  getOhlcv.mockResolvedValueOnce(initial)
  // Returns only the earliest bar, already in the buffer ⇒ no net growth.
  getOhlcv.mockResolvedValueOnce([initial[0]])

  const { result } = renderHistory()
  await waitFor(() => expect(result.current.bars).toHaveLength(3))

  await act(async () => {
    result.current.loadOlder()
    await Promise.resolve()
  })
  await waitFor(() => expect(result.current.reachedStart).toBe(true))
  expect(result.current.bars).toHaveLength(3)
})

it('422 backoff (recovers): halves the span and retries once, prepending on success', async () => {
  const initial = dailyBars(START, 3) // T0 = START
  const older = dailyBars(new Date(START.getTime() - 3 * DAY_MS), 3)
  getOhlcv.mockResolvedValueOnce(initial)
  getOhlcv.mockRejectedValueOnce(new ApiError(422, 'period too large'))
  getOhlcv.mockResolvedValueOnce(older)

  const { result } = renderHistory()
  await waitFor(() => expect(result.current.bars).toHaveLength(3))

  await act(async () => {
    result.current.loadOlder()
    await Promise.resolve()
  })
  await waitFor(() => expect(result.current.isLoadingOlder).toBe(false))

  // The retry used half the span, same end.
  const retry = getOhlcv.mock.calls[2][0]
  expect(retry.end.toISOString()).toBe(START.toISOString())
  expect(retry.start.toISOString()).toBe(
    new Date(START.getTime() - WINDOW_SPAN_MS / 2).toISOString(),
  )
  expect(result.current.olderError).toBeNull()
  expect(result.current.bars).toHaveLength(6)
})

it('422 backoff (still fails): a second 422 sets olderError and leaves reachedStart false', async () => {
  getOhlcv.mockResolvedValueOnce(dailyBars(START, 3))
  getOhlcv.mockRejectedValueOnce(new ApiError(422, 'too large'))
  getOhlcv.mockRejectedValueOnce(new ApiError(422, 'still too large'))

  const { result } = renderHistory()
  await waitFor(() => expect(result.current.bars).toHaveLength(3))

  await act(async () => {
    result.current.loadOlder()
    await Promise.resolve()
  })
  await waitFor(() => expect(result.current.olderError).not.toBeNull())
  expect(result.current.reachedStart).toBe(false)
  expect(getOhlcv).toHaveBeenCalledTimes(3) // initial + 422 + retried 422
})

it('502 surfacing: a non-422 error sets olderError and does NOT set reachedStart (retryable)', async () => {
  getOhlcv.mockResolvedValueOnce(dailyBars(START, 3))
  getOhlcv.mockRejectedValueOnce(new ApiError(502, 'bad gateway'))

  const { result } = renderHistory()
  await waitFor(() => expect(result.current.bars).toHaveLength(3))

  await act(async () => {
    result.current.loadOlder()
    await Promise.resolve()
  })
  await waitFor(() => expect(result.current.olderError).not.toBeNull())
  expect(result.current.reachedStart).toBe(false)
  expect(getOhlcv).toHaveBeenCalledTimes(2) // no retry for a non-422
})

it('refetch: reloads the same window via isRefetching (not isLoading), keeping bars on screen', async () => {
  const first = dailyBars(START, 5)
  const second = dailyBars(START, 7)
  getOhlcv.mockResolvedValueOnce(first)
  const refetchDef = deferred<Bar[]>()
  getOhlcv.mockReturnValueOnce(refetchDef.promise)

  const { result } = renderHistory()
  await waitFor(() => expect(result.current.bars).toHaveLength(5))

  act(() => result.current.refetch())

  // The chart stays populated; the in-flight reload surfaces via isRefetching,
  // NOT the full-screen isLoading skeleton.
  await waitFor(() => expect(result.current.isRefetching).toBe(true))
  expect(result.current.isLoading).toBe(false)
  expect(result.current.bars).toHaveLength(5)

  // It re-fetches the SAME [start, end] window.
  const refetchCall = getOhlcv.mock.calls[1][0]
  expect(refetchCall.start.toISOString()).toBe(START.toISOString())
  expect(refetchCall.end.toISOString()).toBe(END.toISOString())

  await act(async () => {
    refetchDef.resolve(second)
    await refetchDef.promise
  })
  await waitFor(() => expect(result.current.isRefetching).toBe(false))
  expect(result.current.bars).toHaveLength(7)
})

it('re-anchor — symbol change resets the buffer and fetches the new series fresh', async () => {
  const aapl = dailyBars(START, 3)
  const msft = dailyBars(START, 4)
  getOhlcv.mockResolvedValueOnce(aapl)
  getOhlcv.mockResolvedValueOnce(msft)

  const { result, rerender } = renderHistory()
  await waitFor(() => expect(result.current.bars).toHaveLength(3))

  rerender({ symbol: 'MSFT', timeframe: TF, start: START, end: END })
  await waitFor(() => expect(result.current.bars).toHaveLength(4))

  const lastCall = getOhlcv.mock.calls[getOhlcv.mock.calls.length - 1][0]
  expect(lastCall.symbol).toBe('MSFT')
  expect(lastCall.start.toISOString()).toBe(START.toISOString())
  expect(lastCall.end.toISOString()).toBe(END.toISOString())
  expect(result.current.reachedStart).toBe(false)
  // The AAPL bars are gone — every bar is from the MSFT fetch.
  expect(result.current.bars).toEqual(msft)
})

it('re-anchor — overlapping range keeps the buffer and fetches only the missing older edge', async () => {
  // Initial buffer exactly spans [START, END] (A = START, B = END).
  const span = dailyBars(START, 31) // day 0..30 → first=START, last=END
  expect(span[span.length - 1].event_ts).toBe(END.toISOString())
  getOhlcv.mockResolvedValueOnce(span)
  const edge = dailyBars(new Date(START.getTime() - 10 * DAY_MS), 10) // [-10d, -1d]
  getOhlcv.mockResolvedValueOnce(edge)

  const { result, rerender } = renderHistory()
  await waitFor(() => expect(result.current.bars).toHaveLength(31))

  const newStart = new Date(START.getTime() - 10 * DAY_MS)
  rerender({ symbol: SYMBOL, timeframe: TF, start: newStart, end: END })
  await waitFor(() => expect(result.current.bars).toHaveLength(41))

  // Exactly one edge fetch, for the older rim [A-Δ, A] — NOT the whole window.
  expect(getOhlcv).toHaveBeenCalledTimes(2)
  const edgeCall = getOhlcv.mock.calls[1][0]
  expect(edgeCall.start.toISOString()).toBe(newStart.toISOString())
  expect(edgeCall.end.toISOString()).toBe(START.toISOString()) // A = buffer earliest
})

// ── Initial-load history clamp (over-horizon 422 → retry clamped to the cap) ──

const TF_15M = '15m'
const CAP_DAYS_15M = 60
const CAP_MS_15M = CAP_DAYS_15M * DAY_MS
// A window wider than the 15m Yahoo horizon (~60d) so the initial request 422s.
const WIDE_END = new Date('2026-05-01T00:00:00.000Z')
const WIDE_START = new Date(WIDE_END.getTime() - 365 * DAY_MS)

function fifteenMinBars(from: Date, count: number): Bar[] {
  return Array.from({ length: count }, (_, i) =>
    bar(new Date(from.getTime() + i * 15 * 60_000).toISOString(), 100 + i),
  )
}

it('history clamp: an over-horizon 15m 422 retries once clamped to the 60d cap and shows what is available', async () => {
  const clamped = fifteenMinBars(new Date(WIDE_END.getTime() - CAP_MS_15M), 4)
  getOhlcv.mockRejectedValueOnce(new ApiError(422, 'spans 365d but Yahoo serves only ~60d'))
  getOhlcv.mockResolvedValueOnce(clamped)

  const { result } = renderHistory({
    symbol: SYMBOL,
    timeframe: TF_15M,
    start: WIDE_START,
    end: WIDE_END,
  })

  await waitFor(() => expect(result.current.historyClampedDays).toBe(CAP_DAYS_15M))
  expect(result.current.error).toBeNull()
  expect(result.current.isLoading).toBe(false)
  expect(result.current.bars).toHaveLength(4)

  // Exactly two calls: the doomed wide window, then the clamped [end - 60d, end].
  expect(getOhlcv).toHaveBeenCalledTimes(2)
  const wide = getOhlcv.mock.calls[0][0]
  expect(wide.start.toISOString()).toBe(WIDE_START.toISOString())
  const retry = getOhlcv.mock.calls[1][0]
  expect(retry.end.toISOString()).toBe(WIDE_END.toISOString())
  expect(retry.start.toISOString()).toBe(new Date(WIDE_END.getTime() - CAP_MS_15M).toISOString())
})

it('history clamp: a 422 on a non-capped timeframe (1d) still surfaces the error, no retry', async () => {
  getOhlcv.mockRejectedValueOnce(new ApiError(422, 'some other 422'))

  const { result } = renderHistory() // default props are 1d (cap = null)

  await waitFor(() => expect(result.current.error).not.toBeNull())
  expect(result.current.historyClampedDays).toBeNull()
  expect(getOhlcv).toHaveBeenCalledTimes(1) // no clamped retry
})

it('history clamp: a within-cap 15m load succeeds unclamped — no retry, no notice', async () => {
  const withinStart = new Date(WIDE_END.getTime() - 30 * DAY_MS) // 30d < 60d cap
  getOhlcv.mockResolvedValueOnce(fifteenMinBars(withinStart, 5))

  const { result } = renderHistory({
    symbol: SYMBOL,
    timeframe: TF_15M,
    start: withinStart,
    end: WIDE_END,
  })

  await waitFor(() => expect(result.current.isLoading).toBe(false))
  expect(result.current.bars).toHaveLength(5)
  expect(result.current.historyClampedDays).toBeNull()
  expect(getOhlcv).toHaveBeenCalledTimes(1)
})

it('re-anchor — disjoint range resets the buffer and fetches the new window fresh', async () => {
  const first = dailyBars(START, 5)
  // A window entirely after the buffer extent (no overlap).
  const farStart = new Date(END.getTime() + 60 * DAY_MS)
  const farEnd = new Date(farStart.getTime() + WINDOW_SPAN_MS)
  const second = dailyBars(farStart, 6)
  getOhlcv.mockResolvedValueOnce(first)
  getOhlcv.mockResolvedValueOnce(second)

  const { result, rerender } = renderHistory()
  await waitFor(() => expect(result.current.bars).toHaveLength(5))

  rerender({ symbol: SYMBOL, timeframe: TF, start: farStart, end: farEnd })
  await waitFor(() => expect(result.current.bars).toHaveLength(6))

  const lastCall = getOhlcv.mock.calls[getOhlcv.mock.calls.length - 1][0]
  expect(lastCall.start.toISOString()).toBe(farStart.toISOString())
  expect(lastCall.end.toISOString()).toBe(farEnd.toISOString())
  expect(result.current.bars).toEqual(second) // old bars gone
})
