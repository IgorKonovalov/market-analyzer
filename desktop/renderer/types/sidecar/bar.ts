/**
 * Hand-written mirror of `src/market_analyser/data/types.py::Bar`.
 *
 * Replace with the auto-generated output of `desktop/scripts/gen-types.ts` once
 * that script lands (Plan 0001 phase 4 listed it as a deliverable but the script
 * was not built). Until then, keep this file in sync with the Pydantic model.
 */
export interface Bar {
  symbol: string
  timeframe: string
  /** ISO 8601 UTC timestamp emitted by FastAPI's JSON encoder. */
  event_ts: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  source: string
}
