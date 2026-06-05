/**
 * The renderer's single source of truth for the timeframe vocabulary (Plan 0047
 * phase 5).
 *
 * Mirrors the backend canonical set in `src/market_analyser/data/timeframes.py`
 * (and `annotations/types.py::SUPPORTED_TIMEFRAMES = {15m, 1h, 4h, 1d, 1w}`),
 * which is what the MCP and HTTP boundaries actually accept — the sidecar
 * rejects anything outside it. Order is cadence-ascending so the dropdown reads
 * 15m → 1w like the backend's `supported_timeframes_label()`.
 *
 * NOTE: this is hand-maintained, not generated. The `gen-types` pipeline only
 * emits the FastAPI `response_model` schemas in its EMIT allowlist; the timeframe
 * registry is not on the HTTP surface, so there is nothing for it to generate.
 * If `data/timeframes.py` gains or drops a timeframe, update this list to match —
 * the parity is guarded by `timeframes.test.ts` (which pins the expected set so a
 * silent drift fails a test) rather than by a build step.
 */

/** Supported timeframes, cadence-ascending — the exact set the sidecar accepts. */
export const TIMEFRAMES = ['15m', '1h', '4h', '1d', '1w'] as const

export type Timeframe = (typeof TIMEFRAMES)[number]

/** Membership set for runtime narrowing (SSE payloads, free-form strings). */
export const KNOWN_TIMEFRAMES: ReadonlySet<string> = new Set(TIMEFRAMES)

/** The default selected timeframe (daily — the most common analysis cadence). */
export const DEFAULT_TIMEFRAME: Timeframe = '1d'

/** Type guard: is `value` one of the supported timeframes? */
export function isTimeframe(value: string): value is Timeframe {
  return KNOWN_TIMEFRAMES.has(value)
}
