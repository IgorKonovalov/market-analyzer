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

/** `backtest`/`forecast` are absent on the wire (exclude_none) when a flat
 * recommendation lacks that leg — hence `.nullish()`, not `.nullable()`. */
const recommendationBasisSchema = z.object({
  conditions: z.array(z.string()),
  signals: z.array(z.string()),
  backtest: z.record(basisValueSchema).nullish(),
  forecast: z.record(basisValueSchema).nullish(),
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
})

export const recommendationCompletedPayloadSchema = z.object({
  recommendation: recommendationSchema,
}) satisfies z.ZodType<RecommendationCompletedPayloadV1>
