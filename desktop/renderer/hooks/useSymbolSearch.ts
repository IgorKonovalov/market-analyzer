/**
 * Debounced symbol search for the `SymbolPicker` autocomplete (Plan 0024
 * phase 3). Re-queries `GET /search` through the typed client whenever the
 * trimmed query changes and is non-empty, but only after the query has been
 * stable for `debounceMs` — so typing "BTCUSD" character-by-character fires
 * far fewer requests than keystrokes.
 *
 * Stale-response guard: each fired request takes a monotonic id; a response is
 * only applied if its id is still the latest. So a slow earlier request that
 * resolves after a newer one cannot clobber the newer query's results. The
 * per-effect `cancelled` flag covers unmount/re-trigger on top of that.
 *
 * No React Query dep — same small-hook posture as `useOhlcv` / `useAnnotationsPoll`.
 */
import { useEffect, useRef, useState } from 'react'

import { api } from '../api/client'
import type { SymbolInfo } from '../types/sidecar/symbol-info'

export interface UseSymbolSearchResult {
  results: SymbolInfo[]
  isSearching: boolean
  error: Error | null
}

const DEFAULT_DEBOUNCE_MS = 250

export function useSymbolSearch(
  query: string,
  debounceMs: number = DEFAULT_DEBOUNCE_MS,
): UseSymbolSearchResult {
  const [results, setResults] = useState<SymbolInfo[]>([])
  const [isSearching, setIsSearching] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  // Monotonic request id; only the latest in-flight response is applied.
  const latestRequestId = useRef(0)

  const trimmed = query.trim()

  useEffect(() => {
    if (trimmed.length === 0) {
      // A cleared box drops results immediately and fires no request.
      setResults([])
      setIsSearching(false)
      setError(null)
      return
    }

    let cancelled = false
    const timer = setTimeout(() => {
      const requestId = (latestRequestId.current += 1)
      setIsSearching(true)
      setError(null)

      api
        .searchSymbols(trimmed)
        .then((found) => {
          if (cancelled || requestId !== latestRequestId.current) return
          setResults(found)
          setIsSearching(false)
        })
        .catch((err: unknown) => {
          if (cancelled || requestId !== latestRequestId.current) return
          setError(err instanceof Error ? err : new Error(String(err)))
          setIsSearching(false)
        })
    }, debounceMs)

    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [trimmed, debounceMs])

  return { results, isSearching, error }
}
