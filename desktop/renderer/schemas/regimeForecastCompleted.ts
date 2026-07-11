/**
 * Zod schema for the `regime_forecast.completed v1` SSE payload (Plan 0077
 * phase 6, ADR-0070).
 *
 * Validated at runtime in the dispatcher before it reaches any renderer state,
 * the same discipline as the other forecast events. The honest-uncertainty
 * contract means a no-edge verdict travels as `beats_baseline=false` with the
 * persistence baseline as the honest default (the regime stays), never a
 * fabricated transition distribution.
 *
 * Wire semantics (the bus dumps with `exclude_none`): `current_regime`,
 * `transition_probs`, `score_margin`, and `provenance` are ABSENT (not null)
 * when None — hence `.nullish()`. `transition_probs` serialises as an object
 * keyed by the `RegimeState` taxonomy. The schema is `satisfies`-pinned to the
 * hand-written TS mirror in `types/events.ts`, whose parity against the pydantic
 * source of truth is guarded by `types/events.test.ts`.
 */
import { z } from 'zod'

import { forecastProvenanceSchema } from './forecastCompleted'
import type { RegimeForecastCompletedPayloadV1 } from '../types/events'

/** The 6-value regime taxonomy (trend axis × quiet/volatile axis). */
export const regimeStateSchema = z.enum([
  'down_quiet',
  'down_volatile',
  'sideways_quiet',
  'sideways_volatile',
  'up_quiet',
  'up_volatile',
])

const regimeFoldScoreSchema = z.object({
  fold_index: z.number(),
  n_test: z.number(),
  model_brier: z.number().nullish(),
  persistence_brier: z.number().nullish(),
})

const regimeValidationSchema = z.object({
  horizon_bars: z.number(),
  n_splits: z.number(),
  n_scored: z.number(),
  model_brier: z.number().nullish(),
  persistence_brier: z.number().nullish(),
  score_margin: z.number().nullish(),
  beats_baseline: z.boolean(),
  folds: z.array(regimeFoldScoreSchema),
})

const regimeForecastSchema = z
  .object({
    symbol: z.string().min(1),
    timeframe: z.string().min(1),
    as_of_bar_ts: z.string(),
    horizon_bars: z.number().int().min(1),
    current_regime: regimeStateSchema.nullish(),
    // Object keyed by RegimeState; a probability is only a probability in [0, 1].
    transition_probs: z.record(regimeStateSchema, z.number().min(0).max(1)).nullish(),
    beats_baseline: z.boolean(),
    score_margin: z.number().nullish(),
    validation: regimeValidationSchema,
    provenance: forecastProvenanceSchema.nullish(),
  })
  .strict()

export const regimeForecastCompletedPayloadSchema = z
  .object({
    forecast: regimeForecastSchema,
  })
  .strict() satisfies z.ZodType<RegimeForecastCompletedPayloadV1>
