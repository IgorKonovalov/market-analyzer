/**
 * Tracks the in-flight backfill state for the chart's current (symbol, timeframe)
 * and drives the auto-refetch (Plan 0013 phase 4).
 *
 * Subscribes to `backfillBus` (which `App.tsx` feeds from the single
 * `useEventStream`). Filters every event on `(symbol, timeframe)` so a backfill
 * for a different symbol never touches this chart's spinner. On `backfilled` it
 * calls `refetch()` so the chart re-reads the freshly-cached bars via GET /ohlcv;
 * that handler is unconditional on whether a `started` was seen first, so a fast
 * backfill whose `backfilled` arrives before `started` still refetches.
 *
 * `refetch` is held on a ref so a new callback identity from the parent doesn't
 * force a resubscribe (and lose an in-flight `started` flag).
 */
import { useEffect, useRef, useState } from 'react'

import { subscribeBackfill } from '../handlers/backfillBus'
import type { BackfillFailureReason } from '../types/events'

export interface BackfillError {
  reason: BackfillFailureReason
  message: string
}

export interface UseBackfillStateParams {
  symbol: string
  timeframe: string
  refetch: () => void
}

export interface UseBackfillStateResult {
  isBackfilling: boolean
  error: BackfillError | null
}

export function useBackfillState({
  symbol,
  timeframe,
  refetch,
}: UseBackfillStateParams): UseBackfillStateResult {
  const [isBackfilling, setIsBackfilling] = useState(false)
  const [error, setError] = useState<BackfillError | null>(null)

  const refetchRef = useRef(refetch)
  refetchRef.current = refetch

  useEffect(() => {
    // Reset when the chart switches symbol/timeframe — a stale spinner from the
    // previous key must not bleed into the new one.
    setIsBackfilling(false)
    setError(null)

    const unsubscribe = subscribeBackfill((event) => {
      if (event.payload.symbol !== symbol || event.payload.timeframe !== timeframe) return
      switch (event.kind) {
        case 'started':
          setIsBackfilling(true)
          setError(null)
          return
        case 'backfilled':
          setIsBackfilling(false)
          setError(null)
          refetchRef.current()
          return
        case 'failed':
          setIsBackfilling(false)
          setError({ reason: event.payload.reason, message: event.payload.message })
          return
      }
    })
    return unsubscribe
  }, [symbol, timeframe])

  return { isBackfilling, error }
}
