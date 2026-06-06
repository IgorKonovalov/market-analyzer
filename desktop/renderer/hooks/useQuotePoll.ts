/**
 * Polls `GET /quote` for one symbol so the price header shows a live,
 * timeframe-independent current price (Plan 0047 phase 6).
 *
 * Keyed on `symbol` only — NOT timeframe — so switching 1h↔1d never refetches
 * and never changes the displayed price (the price tracks the live quote, not
 * the selected OHLCV series' last bar). Polls at a modest cadence (the live
 * quote has no SSE producer) and suspends while the tab is hidden so the
 * renderer doesn't hammer Yahoo offscreen.
 *
 * Failure-tolerant like `useAnnotationsPoll`: a failed poll keeps the previous
 * quote on screen (the header degrades to a dash only when no quote has ever
 * arrived) and surfaces `error`; the next successful poll clears it.
 */
import { useEffect, useState } from 'react'

import { api } from '../api/client'
import type { QuoteResponse } from '../types/sidecar/quote-response'

export const QUOTE_POLL_INTERVAL_MS = 10_000

export interface UseQuotePollParams {
  symbol: string
  /** Test seam — defaults to `QUOTE_POLL_INTERVAL_MS`. */
  intervalMs?: number
}

export interface UseQuotePollResult {
  quote: QuoteResponse | null
  error: Error | null
}

export function useQuotePoll({
  symbol,
  intervalMs = QUOTE_POLL_INTERVAL_MS,
}: UseQuotePollParams): UseQuotePollResult {
  const [quote, setQuote] = useState<QuoteResponse | null>(null)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    let stopped = false
    // Clear the prior symbol's quote so a stale price can't show under a new
    // symbol before its first poll resolves.
    setQuote(null)
    setError(null)

    const tick = async (): Promise<void> => {
      if (stopped) return
      if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return
      try {
        const next = await api.getQuote(symbol)
        if (stopped) return
        setQuote(next)
        setError(null)
      } catch (err: unknown) {
        if (stopped) return
        const e = err instanceof Error ? err : new Error('quote poll failed')
        // Keep the last-known quote on screen; only surface the error.
        setError(e)
      }
    }

    void tick()
    const handle = setInterval(() => void tick(), intervalMs)

    return () => {
      stopped = true
      clearInterval(handle)
    }
  }, [symbol, intervalMs])

  return { quote, error }
}
