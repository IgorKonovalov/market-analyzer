/**
 * Backtests HTTP wrappers (Plan 0008 phase 5).
 *
 * Two endpoints from `src/market_analyser/api/routes/backtests.py`:
 *   - `GET /backtests/{run_id}` returns the full `BacktestResult` (the route
 *     re-merges `spec.json` + `result.json` + `equity_curve.csv` on the disk
 *     into one JSON payload).
 *   - `GET /backtests?symbol=&strategy_id=&limit=` returns the SQLite-indexed
 *     summary projection (one row per persisted run, ordered by `finished_at`
 *     desc).
 *
 * Both routes are renderer-bearer-gated by the central middleware. The
 * sidecar bearer is injected by `sidecarFetch` from `./client`; nothing in
 * this file knows the secret.
 */
import { callJson } from './client'
import type { BacktestResult } from '../types/sidecar/backtest-result'
import type { BacktestRunSummary } from '../types/sidecar/backtest-run-summary'

export function getBacktest(runId: string): Promise<BacktestResult> {
  return callJson<BacktestResult>(`/backtests/${encodeURIComponent(runId)}`)
}

export interface ListBacktestsParams {
  symbol?: string
  strategy_id?: string
  limit?: number
}

export function listBacktests(params: ListBacktestsParams = {}): Promise<BacktestRunSummary[]> {
  const query = new URLSearchParams()
  if (params.symbol !== undefined) query.set('symbol', params.symbol)
  if (params.strategy_id !== undefined) query.set('strategy_id', params.strategy_id)
  if (params.limit !== undefined) query.set('limit', String(params.limit))
  const suffix = query.toString()
  return callJson<BacktestRunSummary[]>(`/backtests${suffix ? `?${suffix}` : ''}`)
}
