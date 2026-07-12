/**
 * Zod schema for the `technical_read.completed v1` SSE payload (Plan 0074
 * phase 3, ADR-0068).
 *
 * Validated at runtime in the dispatcher before it reaches any renderer state,
 * the same discipline as the recommendation/forecast events: a directional call
 * a user might act on outside the app is dropped loudly when malformed, never
 * rendered half-parsed. This is the LESSER advisory tier — a single-indicator
 * read with NO conviction and NO levels — so the schema is deliberately the
 * bare set: any extra field (a conviction, a stop) is rejected by `.strict()`,
 * mirroring the pydantic `extra="forbid"` that is the honesty guarantee.
 *
 * The schema is `satisfies`-pinned to the hand-written TS mirror in
 * `types/events.ts`, whose parity against the pydantic source of truth is
 * guarded by `types/events.test.ts`.
 */
import { z } from 'zod'

import type { TechnicalReadCompletedPayloadV1 } from '../types/events'

const technicalReadIndicatorSchema = z.enum(['supertrend', 'ema_stack', 'macd', 'ichimoku'])

const technicalReadDirectionSchema = z.enum(['long', 'short', 'flat'])

const technicalReadSchema = z
  .object({
    symbol: z.string().min(1),
    timeframe: z.string().min(1),
    as_of_bar_ts: z.string(),
    indicator_id: technicalReadIndicatorSchema,
    direction: technicalReadDirectionSchema,
    regime_state: z.string(),
    rationale: z.array(z.string()),
  })
  .strict()

export const technicalReadCompletedPayloadSchema = z
  .object({
    read: technicalReadSchema,
  })
  .strict() satisfies z.ZodType<TechnicalReadCompletedPayloadV1>
