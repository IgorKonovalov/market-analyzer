/**
 * Zod schema for the `recommendation.scored v1` SSE payload (Plan 0080 phase 5,
 * ADR-0075).
 *
 * The live nudge that a matured advisory recommendation was scored against
 * realized price. Like `recommendation.completed`, it is validated at runtime
 * before it reaches any renderer state (the track-record panel refetches the
 * authoritative aggregate on it) — a malformed payload is dropped loudly in the
 * dispatcher, never acted on half-parsed.
 *
 * `satisfies`-pinned to the hand-written TS mirror in `types/events.ts`, whose
 * own parity with the pydantic source is guarded by `types/events.test.ts`.
 *
 * `forecast_prob` is `.nullish()` — required-but-nullable on the pydantic side
 * and `exclude_none`-stripped from the wire when None (a demoted no-edge
 * forecast). `direction` is long/short and `outcome_class` is
 * target_hit/stopped/timeout: only scored directional calls emit this, so the
 * flat/pending values can never appear (an envelope claiming them is malformed).
 */
import { z } from 'zod'

import type { RecommendationScoredPayloadV1 } from '../types/events'

export const recommendationScoredPayloadSchema = z.object({
  symbol: z.string().min(1),
  timeframe: z.string().min(1),
  strategy_id: z.string().min(1),
  direction: z.enum(['long', 'short']),
  as_of_bar_ts: z.string(),
  horizon_bars: z.number(),
  conviction: z.number(),
  forecast_prob: z.number().nullish(),
  outcome_class: z.enum(['target_hit', 'stopped', 'timeout']),
  realized_return: z.number(),
  realized_r: z.number(),
  directional_correct: z.boolean(),
  scored_at: z.string(),
}) satisfies z.ZodType<RecommendationScoredPayloadV1>
