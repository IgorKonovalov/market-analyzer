/**
 * Polls GET /annotations every 1000 ms for the active chart window. Suspends
 * polling when the tab is hidden so the renderer doesn't burn CPU offscreen.
 * Plan 0006 phase 6.
 *
 * The hook is deliberately silent about loading state — annotations layer on
 * top of the candle series, never block the chart, and never render a spinner.
 * If a poll fails, the previous annotation list stays on screen and the
 * caller can choose to surface `error`; the next successful poll clears it.
 */
import { useEffect, useState } from 'react'

import { api } from '../api/client'
import type { Annotation } from '../types/sidecar/annotation'

export const ANNOTATIONS_POLL_INTERVAL_MS = 1000

export interface UseAnnotationsPollParams {
  symbol: string
  timeframe: string
  start: Date
  end: Date
  /** Test seam — defaults to `ANNOTATIONS_POLL_INTERVAL_MS`. */
  intervalMs?: number
}

export interface UseAnnotationsPollResult {
  annotations: Annotation[]
  error: Error | null
}

export function useAnnotationsPoll({
  symbol,
  timeframe,
  start,
  end,
  intervalMs = ANNOTATIONS_POLL_INTERVAL_MS,
}: UseAnnotationsPollParams): UseAnnotationsPollResult {
  const [annotations, setAnnotations] = useState<Annotation[]>([])
  const [error, setError] = useState<Error | null>(null)

  // Track primitives in the deps array so a stable {symbol,timeframe,start,end}
  // object doesn't churn the effect on every parent re-render.
  const startMs = start.getTime()
  const endMs = end.getTime()

  useEffect(() => {
    let stopped = false

    const tick = async (): Promise<void> => {
      if (stopped) return
      if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return
      try {
        const next = await api.getAnnotations({
          symbol,
          timeframe,
          start: new Date(startMs),
          end: new Date(endMs),
        })
        if (stopped) return
        setAnnotations(next)
        setError(null)
      } catch (err: unknown) {
        if (stopped) return
        const e = err instanceof Error ? err : new Error('annotations poll failed')
        setError(e)
      }
    }

    // Fire once immediately so the chart picks up existing annotations on
    // mount without waiting for the first interval boundary.
    void tick()
    const handle = setInterval(() => void tick(), intervalMs)

    return () => {
      stopped = true
      clearInterval(handle)
    }
  }, [symbol, timeframe, startMs, endMs, intervalMs])

  return { annotations, error }
}
