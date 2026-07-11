/**
 * Zod schema for the `recommendation.completed v1` SSE payload (Plan 0039
 * phase 2).
 *
 * Unlike the chart events (whose payloads are cast and whose failure mode is
 * a mis-drawn overlay), a recommendation renders levels and a conviction
 * number the user may act on outside the app — so this payload is validated
 * at runtime before it reaches any renderer state, per the plan's done-when.
 * A payload that fails the schema is dropped loudly in the dispatcher, never
 * rendered half-parsed.
 *
 * The schema is `satisfies`-pinned to the hand-written TS mirror in
 * `types/events.ts`, so the compiler rejects drift between the two; the
 * mirror itself is parity-guarded against the pydantic source of truth by
 * `types/events.test.ts`.
 *
 * `label: z.literal('advisory')` is the ADR-0029 boundary at the renderer
 * edge: an envelope claiming any other label is malformed by definition and
 * never reaches the view.
 */
import { z } from 'zod'

import type { RecommendationCompletedPayloadV1 } from '../types/events'

/** Mirror of the pydantic `BasisValue` grain: flat scalars only. */
const basisValueSchema = z.union([z.number(), z.string(), z.boolean(), z.null()])

/** One recorded fusion gate (Plan 0063 / ADR-0058). `threshold`/`actual` are
 * `exclude_none`-stripped from the wire when None (a recorded fact with no
 * pass bar) — hence `.nullish()`. */
const fusionCheckSchema = z.object({
  leg: z.enum(['forecast', 'signal', 'backtest', 'conditions', 'alignment']),
  check: z.string(),
  threshold: basisValueSchema.nullish(),
  actual: basisValueSchema.nullish(),
  passed: z.boolean(),
  // Plan 0077 phase 5 (ADR-0071): defaulted True on the pydantic side, so never
  // None and always on the wire. A `gating=false` check is recorded but does not
  // block — the direction-leg demotion flips the four direction checks to
  // non-gating below the skill-margin threshold.
  gating: z.boolean(),
})

/** The demoted direction leg's gating status (Plan 0077 phase 5, ADR-0071).
 * Travels on every verdict; below the skill-margin threshold `gating` is false
 * and the leg is advisory, not a vote or a veto. `skill_margin` is absent on the
 * wire (exclude_none) when the forecast shipped no scored edge. */
const directionLegStatusSchema = z.object({
  present: z.boolean(),
  gating: z.boolean(),
  skill_margin: z.number().nullish(),
})

/** The non-voting volatility inputs to a directional call (Plan 0077 phase 5).
 * `size_factor` is a bounded relative inverse-vol multiplier; `vol_source` says
 * which reading drove it. `vol_used`/`stop_vol_distance` are absent on the wire
 * when no volatility drove the call. */
const volatilitySizingSchema = z.object({
  size_factor: z.number(),
  vol_used: z.number().nullish(),
  vol_source: z.enum(['model', 'baseline', 'none']),
  stop_vol_distance: z.number().nullish(),
})

/** The non-voting regime context of a directional call (Plan 0077 phase 5).
 * Feeds conviction only. `current_regime` is absent on the wire when undefined;
 * `conviction_factor` is the bounded (0, 1] multiplier applied. */
const regimeContextSchema = z.object({
  current_regime: z.string().nullish(),
  trusted: z.boolean(),
  conviction_factor: z.number(),
})

/** One `{code, params}` reason-code (Plan 0069 / ADR-0063). `params` values are
 * raw numbers or strings — the renderer formats numbers `en-US`. `params` has a
 * `{}` default and is always present on the wire. */
const reasonCodeSchema = z.object({
  code: z.string(),
  params: z.record(z.union([z.number(), z.string()])),
})

/** `backtest`/`forecast` are absent on the wire (exclude_none) when a flat
 * recommendation lacks that leg — hence `.nullish()`, not `.nullable()`.
 * `checks` (Plan 0063) has a non-None default and is always present.
 * `condition_codes`/`signal_codes` (Plan 0069 phase 4b) are the translatable
 * mirrors of `conditions`/`signals`, defaulted `()` and always on the wire. */
const recommendationBasisSchema = z.object({
  conditions: z.array(z.string()),
  signals: z.array(z.string()),
  backtest: z.record(basisValueSchema).nullish(),
  forecast: z.record(basisValueSchema).nullish(),
  checks: z.array(fusionCheckSchema),
  condition_codes: z.array(reasonCodeSchema),
  signal_codes: z.array(reasonCodeSchema),
})

const recommendationSchema = z.object({
  symbol: z.string().min(1),
  timeframe: z.string().min(1),
  direction: z.enum(['long', 'short', 'flat']),
  // Serialised `(low, high)` tuple; absent on the wire when flat.
  entry_zone: z.tuple([z.number(), z.number()]).nullish(),
  stop: z.number().nullish(),
  targets: z.array(z.number()),
  conviction: z.number().min(0).max(1),
  rationale: z.array(z.string()),
  basis: recommendationBasisSchema,
  label: z.literal('advisory'),
  as_of_bar_ts: z.string(),
  reason_codes: z.array(reasonCodeSchema),
  // Plan 0077 phase 5 (ADR-0071): non-voting forecast inputs + the demoted
  // direction leg. `sizing`/`regime_context` shape a directional call and are
  // absent on the wire (exclude_none) for a flat verdict; `direction_leg`
  // travels on every verdict. None of them can flip or manufacture a direction.
  sizing: volatilitySizingSchema.nullish(),
  regime_context: regimeContextSchema.nullish(),
  direction_leg: directionLegStatusSchema.nullish(),
})

export const recommendationCompletedPayloadSchema = z.object({
  recommendation: recommendationSchema,
}) satisfies z.ZodType<RecommendationCompletedPayloadV1>
