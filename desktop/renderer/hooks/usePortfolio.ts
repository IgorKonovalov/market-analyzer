/**
 * Fetch-state machine for the cross-venue portfolio (Plan 0043 phase 2).
 *
 * Imperative, like `useWalletPnl`: the view calls `load(wallet?, includeBasis?)`
 * on mount / submit, and the hook drives `GET /portfolio` through the typed
 * client (which Zod-validates the payload). Four-state shape:
 *   `{status: 'idle'}` → `{status: 'loading'}` → `{status: 'ready', result}`
 *                                              ↘ `{status: 'error', error}`
 *
 * A monotonic request id guards a stale in-flight response from overwriting a
 * newer one (a fast re-load, or an unmount) — the last `load` wins.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../api/client'
import type { PortfolioSurface } from '../schemas/portfolio'

export type UsePortfolioState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; result: PortfolioSurface }
  | { status: 'error'; error: Error }

export interface UsePortfolio {
  state: UsePortfolioState
  /** Fetch the portfolio. A `wallet` switches the DeFi leg on; omit it for the
   * CEX + manual legs only. `includeDefiBasis=false` skips the basis replay. */
  load: (wallet?: string, includeDefiBasis?: boolean) => void
}

export function usePortfolio(): UsePortfolio {
  const [state, setState] = useState<UsePortfolioState>({ status: 'idle' })
  const requestId = useRef(0)
  useEffect(
    () => () => {
      requestId.current += 1
    },
    [],
  )

  const load = useCallback((wallet?: string, includeDefiBasis?: boolean): void => {
    const id = (requestId.current += 1)
    setState({ status: 'loading' })
    api
      .getPortfolio({ wallet, includeDefiBasis })
      .then((result) => {
        if (id === requestId.current) setState({ status: 'ready', result })
      })
      .catch((err: unknown) => {
        if (id !== requestId.current) return
        setState({ status: 'error', error: err instanceof Error ? err : new Error(String(err)) })
      })
  }, [])

  return { state, load }
}
