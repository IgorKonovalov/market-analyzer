/**
 * Zod schema for the `prediction.screen_completed v1` SSE payload (Plan 0078
 * phase 3, ADR-0041/0029).
 *
 * Validated at runtime in the dispatcher before it reaches any renderer state,
 * the same discipline as the recommendation/forecast/technical-read events: a
 * convergence opportunity carries an implied return a user might act on outside
 * the app, so a malformed payload is dropped loudly, never rendered half-parsed.
 *
 * The opportunities are FACTS with their risks attached, never a call — the
 * shape has no direction/size/action field, and `.strict()` (mirroring the
 * pydantic `extra="forbid"`/frozen models) rejects any extra field so an
 * advice-shaped key can never sneak onto a rendered row.
 *
 * `liquidity_caution`/`volume_usd` are None-defaulted on the pydantic model and
 * the bus dumps with `exclude_none`, so they are absent (or null) on the wire
 * when the book is deep/unknown — `.nullish()` accepts both. The schema is
 * `satisfies`-pinned to the hand-written TS mirror in `types/events.ts`, whose
 * parity against the pydantic source of truth is guarded by `types/events.test.ts`.
 */
import { z } from 'zod'

import type { PredictionScreenCompletedPayloadV1 } from '../types/events'

const resolutionRiskLevelSchema = z.enum(['low', 'medium', 'high'])

const resolutionRiskSchema = z
  .object({
    level: resolutionRiskLevelSchema,
    reasons: z.array(z.string()),
  })
  .strict()

const convergenceOpportunitySchema = z
  .object({
    market_id: z.string().min(1),
    question: z.string().min(1),
    outcome_label: z.string().min(1),
    implied_probability: z.number(),
    implied_return_if_right: z.number(),
    time_to_resolution: z.string(),
    capital_lockup_note: z.string(),
    liquidity_caution: z.string().nullish(),
    resolution_risk: resolutionRiskSchema,
    volume_usd: z.number().nullish(),
    closes_at: z.string(),
    queried_at: z.string(),
    source: z.string(),
  })
  .strict()

export const predictionScreenCompletedPayloadSchema = z
  .object({
    query: z.string(),
    opportunities: z.array(convergenceOpportunitySchema),
    queried_at: z.string(),
    source: z.string(),
  })
  .strict() satisfies z.ZodType<PredictionScreenCompletedPayloadV1>
