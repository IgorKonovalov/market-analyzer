/**
 * Fetch-state machine for a single `BacktestResult` (Plan 0008 phase 5).
 *
 * Two trigger paths:
 *   1. **Event-driven** — subscribes to `run.completed v1` envelopes via
 *      `runCompletedBus`. When `payload.kind === 'backtest'` arrives, the
 *      hook fetches `GET /backtests/{run_id}`. Analysis / defi completions
 *      are filtered out (the bus carries them too; this hook only cares
 *      about backtests).
 *   2. **Click-through** — `runId` prop. When set (e.g. RecentBacktestsView
 *      hands a `run_id` to BacktestView for explicit display), the hook
 *      fetches immediately, independent of the event stream.
 *
 * State shape per Plan 0008 phase 5 done-when:
 *   `{status: 'idle'}` → `{status: 'loading'}` → `{status: 'ready', result}`
 *                                              ↘ `{status: 'error', error}`
 *
 * Error message is wrapped to include the run_id so the UI can render a
 * traceable failure (e.g. "fetch failed for run abc123").
 */
import { useEffect, useState } from 'react'

import { getBacktest } from '../api/backtests'
import { subscribeRunCompleted } from '../handlers/runCompletedBus'
import type { BacktestResult } from '../types/sidecar/backtest-result'

export type UseBacktestResultState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; result: BacktestResult }
  | { status: 'error'; error: Error }

export interface UseBacktestResultOptions {
  /** Default `true`. When `false`, the hook does not subscribe to the bus. */
  enabled?: boolean
  /** When non-null, fetch this run_id immediately. Used by click-through
   * from RecentBacktestsView. Setting back to `null` does not clear state. */
  runId?: string | null
}

export function useBacktestResult(options: UseBacktestResultOptions = {}): UseBacktestResultState {
  const { enabled = true, runId = null } = options
  const [state, setState] = useState<UseBacktestResultState>({ status: 'idle' })

  // Event-driven path. Subscribe once per `enabled` toggle; cleanup
  // unregisters via the function returned by `subscribeRunCompleted`.
  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    const unsubscribe = subscribeRunCompleted((payload) => {
      if (cancelled) return
      if (payload.kind !== 'backtest') return
      void fetchInto(payload.run_id, setState, () => cancelled)
    })
    return () => {
      cancelled = true
      unsubscribe()
    }
  }, [enabled])

  // Click-through path. Refetch whenever `runId` changes to a non-null id.
  useEffect(() => {
    if (runId === null || runId === undefined) return
    let cancelled = false
    void fetchInto(runId, setState, () => cancelled)
    return () => {
      cancelled = true
    }
  }, [runId])

  return state
}

async function fetchInto(
  runId: string,
  setState: (next: UseBacktestResultState) => void,
  isCancelled: () => boolean,
): Promise<void> {
  setState({ status: 'loading' })
  try {
    const result = await getBacktest(runId)
    if (isCancelled()) return
    setState({ status: 'ready', result })
  } catch (err) {
    if (isCancelled()) return
    const original = err instanceof Error ? err.message : String(err)
    const error = new Error(`backtest ${runId}: ${original}`)
    setState({ status: 'error', error })
  }
}
