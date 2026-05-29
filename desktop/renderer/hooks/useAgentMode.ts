/**
 * Agent-mode toggle state (Plan 0014, ADR-0021). Reads the persisted toggle
 * once on mount via `GET /agent_mode` and exposes `setEnabled`, which PUTs the
 * new value and flips local state only on a 2xx — a failed PUT leaves the
 * toggle where it was and surfaces the error, so the UI never shows a state the
 * sidecar didn't accept.
 *
 * It does NOT PUT on mount: the toggle persists server-side, so mounting the
 * viewer must not reset the user's choice. Same small-hook posture as
 * `useOhlcv` / `useSymbolSearch` — no React Query.
 */
import { useCallback, useEffect, useState } from 'react'

import { api } from '../api/client'

export interface UseAgentModeResult {
  enabled: boolean
  setEnabled: (next: boolean) => void
  error: Error | null
}

export function useAgentMode(): UseAgentModeResult {
  const [enabled, setEnabledState] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .getAgentMode()
      .then((state) => {
        if (!cancelled) setEnabledState(state.enabled)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err : new Error(String(err)))
      })
    return () => {
      cancelled = true
    }
  }, [])

  const setEnabled = useCallback((next: boolean) => {
    setError(null)
    api
      .setAgentMode(next)
      .then((state) => {
        // Trust the server's echoed state, not the optimistic `next`.
        setEnabledState(state.enabled)
      })
      .catch((err: unknown) => {
        // Leave `enabled` unchanged — never show a state the sidecar rejected.
        setError(err instanceof Error ? err : new Error(String(err)))
      })
  }, [])

  return { enabled, setEnabled, error }
}
