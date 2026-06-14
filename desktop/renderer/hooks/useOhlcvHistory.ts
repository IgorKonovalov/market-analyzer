/**
 * Accumulating OHLCV buffer for the chart, keyed by (symbol, timeframe).
 *
 * Subsumes `useOhlcv`'s initial-window role for `OhlcvView` and adds backward
 * paging: `loadOlder()` fetches one viewport-width chunk of bars older than the
 * buffer's earliest and prepends them, so scrolling left past the loaded window
 * streams in history instead of hitting blank canvas (Plan 0030).
 *
 * The buffer is sorted ascending by `event_ts` and deduped on `event_ts`. An
 * older fetch that returns no NEW bars (empty, or all duplicates of what's
 * already buffered) means the start of available history — true data start, or
 * the timeframe's Yahoo horizon — has been hit; `reachedStart` latches and
 * further `loadOlder()` calls no-op until the buffer key changes. The data
 * layer's 732-day per-fetch cap is respected by clamping the chunk span to 700
 * days and, on a 422, halving the span and retrying once.
 *
 * It deliberately does NOT wrap `useOhlcv`: `loadOlder` needs imperative
 * fetches against an arbitrary older window, so it calls `api.getOhlcv`
 * directly. `useOhlcv` stays in the tree for any other consumer.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError, api } from '../api/client'
import type { Bar } from '../types/sidecar/bar'

export interface UseOhlcvHistoryParams {
  symbol: string
  timeframe: string
  start: Date
  end: Date
}

export interface UseOhlcvHistoryResult {
  /** Accumulating buffer, sorted ascending by `event_ts`, deduped on `event_ts`. */
  bars: Bar[] | null
  /** Initial-window load (parity with `useOhlcv`). */
  isLoading: boolean
  /** A user-triggered `refetch()` of the SAME series is in flight. Distinct from
   * `isLoading`: the existing bars stay on screen (the chart updates in place
   * rather than blanking to a skeleton), so the UI can show a lightweight inline
   * "refreshing" affordance instead. */
  isRefetching: boolean
  /** Initial-window error (parity with `useOhlcv`). */
  error: Error | null
  /** Reload the initial `[start, end]` window from scratch. */
  refetch: () => void
  /** Fetch + prepend one older chunk; no-op while in flight or after `reachedStart`. */
  loadOlder: () => void
  isLoadingOlder: boolean
  olderError: Error | null
  /** Empty / all-duplicate older fetch ⇒ no more history (data start or TF horizon). */
  reachedStart: boolean
}

// The route caps a single fetch at 732 days (Yahoo `_MAX_PERIOD_DAYS`); stay
// comfortably under it so the default chunk never 422s on its own.
const MAX_OLDER_CHUNK_DAYS = 700
const MAX_OLDER_CHUNK_MS = MAX_OLDER_CHUNK_DAYS * 24 * 60 * 60 * 1000

const tsMs = (bar: Bar): number => new Date(bar.event_ts).getTime()

/**
 * Concatenate + dedupe on `event_ts` + sort ascending by instant. Dedup is by
 * the `event_ts` string (the wire identity); ordering uses the parsed instant
 * so a mix of `Z` / `+00:00` offsets can't misorder the join.
 */
function mergeBars(existing: Bar[], incoming: Bar[]): Bar[] {
  if (incoming.length === 0) return existing
  const byTs = new Map<string, Bar>()
  for (const b of existing) byTs.set(b.event_ts, b)
  for (const b of incoming) byTs.set(b.event_ts, b)
  return [...byTs.values()].sort((a, b) => tsMs(a) - tsMs(b))
}

export function useOhlcvHistory({
  symbol,
  timeframe,
  start,
  end,
}: UseOhlcvHistoryParams): UseOhlcvHistoryResult {
  const [bars, setBars] = useState<Bar[] | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isRefetching, setIsRefetching] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [isLoadingOlder, setIsLoadingOlder] = useState(false)
  const [olderError, setOlderError] = useState<Error | null>(null)
  const [reachedStart, setReachedStart] = useState(false)
  const [refetchToken, setRefetchToken] = useState(0)

  const startMs = start.getTime()
  const endMs = end.getTime()

  // Mirrors of state that the imperative `loadOlder` and stale-result guards
  // read synchronously (state is async + stale inside a closure).
  const bufferRef = useRef<Bar[] | null>(null)
  const keyRef = useRef<string | null>(null)
  const refetchTokenRef = useRef(refetchToken)
  const reachedStartRef = useRef(false)
  const loadingOlderRef = useRef(false)
  const mountedRef = useRef(true)
  // Bumped on every full reset so a slow `loadOlder` resolving after a
  // symbol/window change can't merge stale bars into the new buffer.
  const genRef = useRef(0)
  // Default older-chunk span = the initial window's span, clamped under the cap.
  const chunkSpanMsRef = useRef(Math.min(Math.max(endMs - startMs, 0), MAX_OLDER_CHUNK_MS))

  const setBuffer = useCallback((next: Bar[] | null): void => {
    bufferRef.current = next
    setBars(next)
  }, [])

  const refetch = useCallback(() => setRefetchToken((n) => n + 1), [])

  // Initial load + re-anchor on prop changes.
  useEffect(() => {
    const key = `${symbol}|${timeframe}`
    const existing = bufferRef.current
    const reqStartMs = startMs
    const reqEndMs = endMs

    const keyChanged = key !== keyRef.current
    const refetched = refetchToken !== refetchTokenRef.current
    keyRef.current = key
    refetchTokenRef.current = refetchToken

    // Decide reset (replace the whole buffer) vs edge-fill (keep it, fetch the
    // missing rim) vs no-op (the new window already sits inside the buffer).
    let mode: 'reset' | 'edges' | 'noop' = 'reset'
    if (!keyChanged && !refetched && existing && existing.length > 0) {
      const earliestMs = tsMs(existing[0])
      const latestMs = tsMs(existing[existing.length - 1])
      const overlaps = reqStartMs <= latestMs && reqEndMs >= earliestMs
      mode = overlaps ? 'edges' : 'reset'
    }

    let cancelled = false

    if (mode === 'reset') {
      genRef.current += 1
      chunkSpanMsRef.current = Math.min(Math.max(reqEndMs - reqStartMs, 0), MAX_OLDER_CHUNK_MS)
      reachedStartRef.current = false
      setReachedStart(false)
      // A user-triggered `refetch()` of the SAME series keeps the existing bars
      // on screen and surfaces the lightweight `isRefetching` flag, so the chart
      // updates in place instead of blanking to the loading skeleton. A symbol
      // switch or a disjoint-window jump still clears the buffer and shows the
      // skeleton — there's nothing valid left to keep showing.
      const keepBuffer = refetched && !keyChanged && !!existing && existing.length > 0
      if (!keepBuffer) setBuffer(null)
      if (keepBuffer) setIsRefetching(true)
      else setIsLoading(true)
      setError(null)
      // A pending older fetch from the previous buffer is now irrelevant.
      loadingOlderRef.current = false
      setIsLoadingOlder(false)
      setOlderError(null)

      api
        .getOhlcv({ symbol, timeframe, start: new Date(reqStartMs), end: new Date(reqEndMs) })
        .then((result) => {
          if (cancelled) return
          setBuffer(mergeBars([], result))
          setIsLoading(false)
          setIsRefetching(false)
        })
        .catch((err: unknown) => {
          if (cancelled) return
          setError(err instanceof Error ? err : new Error(String(err)))
          setIsLoading(false)
          setIsRefetching(false)
        })
      return () => {
        cancelled = true
      }
    }

    if (mode === 'edges') {
      const earliestMs = tsMs(existing![0])
      const latestMs = tsMs(existing![existing!.length - 1])
      const fetches: Array<Promise<Bar[]>> = []
      const needOlder = reqStartMs < earliestMs
      const needNewer = reqEndMs > latestMs
      if (needOlder) {
        fetches.push(
          api.getOhlcv({
            symbol,
            timeframe,
            start: new Date(reqStartMs),
            end: new Date(earliestMs),
          }),
        )
      }
      if (needNewer) {
        fetches.push(
          api.getOhlcv({
            symbol,
            timeframe,
            start: new Date(latestMs),
            end: new Date(reqEndMs),
          }),
        )
      }
      if (fetches.length === 0) return

      // Extending the window earlier means there may be more history again.
      if (needOlder) {
        reachedStartRef.current = false
        setReachedStart(false)
      }
      Promise.all(fetches)
        .then((results) => {
          if (cancelled) return
          setBuffer(mergeBars(bufferRef.current ?? [], results.flat()))
        })
        .catch((err: unknown) => {
          if (cancelled) return
          setError(err instanceof Error ? err : new Error(String(err)))
        })
      return () => {
        cancelled = true
      }
    }

    return undefined
  }, [symbol, timeframe, startMs, endMs, refetchToken, setBuffer])

  const loadOlder = useCallback(() => {
    if (loadingOlderRef.current || reachedStartRef.current) return
    const existing = bufferRef.current
    if (!existing || existing.length === 0) return

    const endDate = new Date(existing[0].event_ts)
    const gen = genRef.current
    loadingOlderRef.current = true
    setIsLoadingOlder(true)
    setOlderError(null)

    const attempt = (spanMs: number, isRetry: boolean): void => {
      const startDate = new Date(endDate.getTime() - spanMs)
      api
        .getOhlcv({ symbol, timeframe, start: startDate, end: endDate })
        .then((older) => {
          if (!mountedRef.current || gen !== genRef.current) return
          const current = bufferRef.current ?? []
          const merged = mergeBars(current, older)
          // No net growth ⇒ start of available history reached.
          if (merged.length === current.length) {
            reachedStartRef.current = true
            setReachedStart(true)
          } else {
            setBuffer(merged)
          }
          setOlderError(null)
          loadingOlderRef.current = false
          setIsLoadingOlder(false)
        })
        .catch((err: unknown) => {
          if (!mountedRef.current || gen !== genRef.current) {
            loadingOlderRef.current = false
            return
          }
          // Span too large: halve once and retry before surfacing an error.
          if (!isRetry && err instanceof ApiError && err.status === 422) {
            attempt(spanMs / 2, true)
            return
          }
          setOlderError(err instanceof Error ? err : new Error(String(err)))
          loadingOlderRef.current = false
          setIsLoadingOlder(false)
        })
    }

    attempt(chunkSpanMsRef.current, false)
  }, [symbol, timeframe, setBuffer])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  return {
    bars,
    isLoading,
    isRefetching,
    error,
    refetch,
    loadOlder,
    isLoadingOlder,
    olderError,
    reachedStart,
  }
}
