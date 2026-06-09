/**
 * The renderer's single source of truth for the timeframe vocabulary (Plan 0047
 * phase 5).
 *
 * Mirrors the backend canonical set in `src/market_analyser/data/timeframes.py`
 * (and `annotations/types.py::SUPPORTED_TIMEFRAMES = {15m, 1h, 4h, 1d, 1w, 1mo}`),
 * which is what the MCP and HTTP boundaries actually accept — the sidecar
 * rejects anything outside it. Order is cadence-ascending so the dropdown reads
 * 15m → 1mo like the backend's `supported_timeframes_label()`.
 *
 * NOTE: this is hand-maintained, not generated. The `gen-types` pipeline only
 * emits the FastAPI `response_model` schemas in its EMIT allowlist; the timeframe
 * registry is not on the HTTP surface, so there is nothing for it to generate.
 * If `data/timeframes.py` gains or drops a timeframe, update this list to match —
 * the parity is guarded by `timeframes.test.ts` (which pins the expected set so a
 * silent drift fails a test) rather than by a build step.
 */

/** Supported timeframes, cadence-ascending — the exact set the sidecar accepts. */
export const TIMEFRAMES = ['15m', '1h', '4h', '1d', '1w', '1mo'] as const

export type Timeframe = (typeof TIMEFRAMES)[number]

/** Membership set for runtime narrowing (SSE payloads, free-form strings). */
export const KNOWN_TIMEFRAMES: ReadonlySet<string> = new Set(TIMEFRAMES)

/** The default selected timeframe (daily — the most common analysis cadence). */
export const DEFAULT_TIMEFRAME: Timeframe = '1d'

/** Type guard: is `value` one of the supported timeframes? */
export function isTimeframe(value: string): value is Timeframe {
  return KNOWN_TIMEFRAMES.has(value)
}

/** Nominal duration of one bar of each timeframe, in milliseconds. Weeks/days are
 * treated as fixed spans (the same approximation the analysis lookback windows
 * use) — precise enough to bound "is this quote inside the latest forming bar's
 * period?" (Plan 0049 phase 10). A calendar month is variable (28–31 days); we
 * use 31 days to match the backend's `bar_duration=timedelta(days=31)` for `1mo`
 * (ADR-0047 / Plan 0050 phase 4.5). The slight overshoot only means a quote
 * early in the next month can still nudge the prior month's forming bar — benign
 * on a monthly chart, and it never rewrites a closed bar (the forming effect only
 * touches the last bar). */
const TIMEFRAME_DURATION_MS: Record<Timeframe, number> = {
  '15m': 15 * 60_000,
  '1h': 60 * 60_000,
  '4h': 4 * 60 * 60_000,
  '1d': 24 * 60 * 60_000,
  '1w': 7 * 24 * 60 * 60_000,
  '1mo': 31 * 24 * 60 * 60_000,
}

/** Nominal bar duration (ms) for a timeframe string, or `null` when unrecognised. */
export function timeframeDurationMs(timeframe: string | undefined): number | null {
  if (timeframe !== undefined && isTimeframe(timeframe)) {
    return TIMEFRAME_DURATION_MS[timeframe]
  }
  return null
}
