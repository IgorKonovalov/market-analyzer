/**
 * Fetch-state machine for a DeFi risk recompute (Plan 0043 phase 2).
 *
 * Imperative, like `usePortfolio`: the risk panel calls `recompute(request)`
 * when the user dials a shock or asks for a conditional probability, and the
 * hook drives `POST /portfolio/risk` through the typed client (Zod-validated).
 * Four-state shape, plus the last request echoed on `ready` so the panel can
 * label which leg/kind it is showing:
 *   `{status: 'idle'}` → `{status: 'loading'}` → `{status: 'ready', result}`
 *                                              ↘ `{status: 'error', error}`
 *
 * A monotonic request id guards a stale response from overwriting a newer one —
 * a fast drag of the shock slider fires many recomputes; the last one wins.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../api/client'
import type { PortfolioRiskResponse, RiskRequest } from '../schemas/portfolio'

export type UsePortfolioRiskState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; result: PortfolioRiskResponse }
  | { status: 'error'; error: Error }

export interface UsePortfolioRisk {
  state: UsePortfolioRiskState
  recompute: (request: RiskRequest) => void
}

export function usePortfolioRisk(): UsePortfolioRisk {
  const [state, setState] = useState<UsePortfolioRiskState>({ status: 'idle' })
  const requestId = useRef(0)
  useEffect(
    () => () => {
      requestId.current += 1
    },
    [],
  )

  const recompute = useCallback((request: RiskRequest): void => {
    const id = (requestId.current += 1)
    setState({ status: 'loading' })
    api
      .recomputeRisk(request)
      .then((result) => {
        if (id === requestId.current) setState({ status: 'ready', result })
      })
      .catch((err: unknown) => {
        if (id !== requestId.current) return
        setState({ status: 'error', error: err instanceof Error ? err : new Error(String(err)) })
      })
  }, [])

  return { state, recompute }
}
