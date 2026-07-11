/**
 * Zod schema for the `volatility_forecast.completed v1` SSE payload (Plan 0077
 * phase 6, ADR-0070).
 *
 * Like `forecast.completed` and `recommendation.completed`, a forecast the user
 * may read as a magnitude is validated at runtime in the dispatcher before it
 * reaches any renderer state — a malformed payload is dropped loudly, never
 * rendered half-parsed. The honest-uncertainty contract (ADR-0030/0070) means a
 * no-edge verdict travels as `beats_baseline=false` with the deterministic
 * baseline surfaced, never a fabricated model number.
 *
 * Wire semantics (the bus dumps with `exclude_none`): the magnitude scalars
 * (`predicted_vol`, `band`, `baseline_vol`, `baseline_kind`, `score_margin`) and
 * `provenance` are ABSENT (not null) when None — hence `.nullish()`. The schema
 * is `satisfies`-pinned to the hand-written TS mirror in `types/events.ts`, whose
 * parity against the pydantic source of truth is guarded by `types/events.test.ts`.
 */
import { z } from 'zod'

import { forecastProvenanceSchema } from './forecastCompleted'
import type { VolatilityForecastCompletedPayloadV1 } from '../types/events'

const baselineKindSchema = z.enum(['ewma', 'persistence'])

const volatilityFoldScoreSchema = z.object({
  fold_index: z.number(),
  n_test: z.number(),
  model_qlike: z.number().nullish(),
  persistence_qlike: z.number().nullish(),
  ewma_qlike: z.number().nullish(),
})

const volatilityValidationSchema = z.object({
  horizon_bars: z.number(),
  n_splits: z.number(),
  n_scored: z.number(),
  model_qlike: z.number().nullish(),
  baseline_qlike: z.number().nullish(),
  baseline_kind: baselineKindSchema.nullish(),
  persistence_qlike: z.number().nullish(),
  ewma_qlike: z.number().nullish(),
  score_margin: z.number().nullish(),
  beats_baseline: z.boolean(),
  folds: z.array(volatilityFoldScoreSchema),
})

const volatilityForecastSchema = z
  .object({
    symbol: z.string().min(1),
    timeframe: z.string().min(1),
    as_of_bar_ts: z.string(),
    horizon_bars: z.number().int().min(1),
    predicted_vol: z.number().nullish(),
    // Serialised `(low, high)` tuple; absent on the wire when no model trained.
    band: z.tuple([z.number(), z.number()]).nullish(),
    baseline_vol: z.number().nullish(),
    baseline_kind: baselineKindSchema.nullish(),
    beats_baseline: z.boolean(),
    score_margin: z.number().nullish(),
    validation: volatilityValidationSchema,
    provenance: forecastProvenanceSchema.nullish(),
  })
  .strict()

export const volatilityForecastCompletedPayloadSchema = z
  .object({
    forecast: volatilityForecastSchema,
  })
  .strict() satisfies z.ZodType<VolatilityForecastCompletedPayloadV1>
