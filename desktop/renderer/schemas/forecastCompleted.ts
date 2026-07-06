/**
 * Zod schema for the `forecast.completed v1` SSE payload (Plan 0037 phase 2).
 *
 * A forecast is the single most over-trusted output the app produces
 * (ADR-0030): a malformed or half-parsed payload rendered as a confident
 * probability is exactly the failure mode the honest-uncertainty contract
 * exists to prevent. So — like `recommendation.completed` (Plan 0039) and
 * `alert.triggered` (Plan 0060) — this payload is validated at runtime in the
 * dispatcher before it reaches any renderer state; a payload that fails the
 * schema is dropped loudly, never rendered.
 *
 * Wire semantics (the bus dumps with `exclude_none`): `prob_*`, `edge_margin`,
 * per-block `provenance`, the validation skill fields, and
 * `SeriesInput.last_point_ts` are ABSENT (not null) when None — hence
 * `.nullish()`. `ForecastProvenance.series_inputs` has a non-None default and
 * is always present (an empty array marks the v1 OHLCV-only feature set).
 *
 * The schema is `satisfies`-pinned to the hand-written TS mirror in
 * `types/events.ts`, so the compiler rejects drift between the two; the
 * mirror itself is parity-guarded against the pydantic source of truth by
 * `types/events.test.ts`.
 */
import { z } from 'zod'

import type { ForecastCompletedPayloadV1 } from '../types/events'

const seriesInputSchema = z.object({
  series_id: z.string(),
  last_point_ts: z.number().nullish(),
})

const foldSkillSchema = z.object({
  fold_index: z.number(),
  n_test: z.number(),
  model_skill: z.number().nullish(),
  persistence_skill: z.number().nullish(),
  majority_skill: z.number().nullish(),
})

const forecastValidationSchema = z.object({
  horizon_bars: z.number(),
  n_splits: z.number(),
  n_scored: z.number(),
  skill: z.number().nullish(),
  baseline_skill: z.number().nullish(),
  persistence_skill: z.number().nullish(),
  majority_skill: z.number().nullish(),
  beats_baseline: z.boolean(),
  folds: z.array(foldSkillSchema),
})

const forecastProvenanceSchema = z.object({
  model_version: z.string().min(1),
  feature_set_id: z.string().min(1),
  training_cutoff: z.string(),
  seed: z.number(),
  lib_versions: z.record(z.string()),
  series_inputs: z.array(seriesInputSchema),
})

/** A probability is only a probability in [0, 1] — anything else is malformed
 * by definition and must never render as a bar. */
const probabilitySchema = z.number().min(0).max(1)

const horizonForecastSchema = z.object({
  horizon_bars: z.number().int().min(1),
  prob_up: probabilitySchema.nullish(),
  prob_down: probabilitySchema.nullish(),
  prob_flat: probabilitySchema.nullish(),
  validation: forecastValidationSchema,
  edge_margin: z.number().nullish(),
  edge_strength: z.enum(['no_edge', 'marginal', 'clear']),
  provenance: forecastProvenanceSchema.nullish(),
})

const multiHorizonForecastResultSchema = z.object({
  symbol: z.string().min(1),
  timeframe: z.string().min(1),
  as_of_bar_ts: z.string(),
  feature_set_id: z.string().min(1),
  horizons: z.array(horizonForecastSchema),
})

export const forecastCompletedPayloadSchema = z.object({
  forecast: multiHorizonForecastResultSchema,
}) satisfies z.ZodType<ForecastCompletedPayloadV1>
