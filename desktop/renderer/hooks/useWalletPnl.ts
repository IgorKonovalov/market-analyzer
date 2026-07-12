/**
 * Fetch-state machine for a wallet's DeFi P&L (Plan 0088 phase 5).
 *
 * Unlike the reactive `useBacktestResult` (driven by SSE + props), this hook is
 * imperative: the view calls `analyze(address, refresh)` on submit / chip click,
 * and the hook drives `POST /defi/pnl` through the typed client. State follows
 * the same four-state shape as the backtest hook:
 *   `{status: 'idle'}` → `{status: 'loading'}` → `{status: 'ready', result}`
 *                                              ↘ `{status: 'error', error}`
 *
 * A monotonic request id guards against a stale in-flight response overwriting a
 * newer one (a fast second Analyze, or an unmount) — the last `analyze` wins.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../api/client'
import type { WalletPnlResponse } from '../types/defiPnl'

export type UseWalletPnlState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; result: WalletPnlResponse }
  | { status: 'error'; error: Error }

export interface UseWalletPnl {
  state: UseWalletPnlState
  /** Fetch the wallet's P&L. `refresh=true` re-pulls from Zerion (slower). */
  analyze: (address: string, refresh?: boolean) => void
}

export function useWalletPnl(): UseWalletPnl {
  const [state, setState] = useState<UseWalletPnlState>({ status: 'idle' })
  // Monotonic id: only the newest request may write state (races / unmount).
  const requestId = useRef(0)
  useEffect(
    () => () => {
      // Invalidate any in-flight request on unmount so a late resolve is dropped.
      requestId.current += 1
    },
    [],
  )

  const analyze = useCallback((address: string, refresh = false): void => {
    const id = (requestId.current += 1)
    setState({ status: 'loading' })
    api
      .getWalletPnl({ address, refresh })
      .then((result) => {
        if (id === requestId.current) setState({ status: 'ready', result })
      })
      .catch((err: unknown) => {
        if (id !== requestId.current) return
        const error = err instanceof Error ? err : new Error(String(err))
        setState({ status: 'error', error })
      })
  }, [])

  return { state, analyze }
}
