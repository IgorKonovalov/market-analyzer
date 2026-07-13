/**
 * Zod schema for the `chart.divergences v1` SSE payload (Plan 0091 phase 9,
 * ADR-0090).
 *
 * Validated at runtime in the dispatcher before it reaches any chart state — a
 * step up from the sibling `chart.trendlines` channel (which casts): a divergence
 * carries structured cross-pane geometry (two pivot lists + an oscillator routing
 * key), and a malformed payload is dropped loudly with a `console.warn` rather
 * than half-drawn. `.strict()` mirrors the pydantic `extra="forbid"` on every
 * level, so an unexpected field is rejected.
 *
 * The schema is `satisfies`-pinned to the hand-written TS mirror in
 * `types/events.ts`, whose parity against the pydantic source of truth is guarded
 * by `types/events.test.ts`.
 */
import { z } from 'zod'

import type { ChartDivergencesPayloadV1 } from '../types/events'

const pivotPointSchema = z
  .object({
    ts: z.string(),
    price: z.number(),
  })
  .strict()

const divergenceKindSchema = z.enum([
  'regular_bullish',
  'regular_bearish',
  'hidden_bullish',
  'hidden_bearish',
])

const divergenceOscillatorSchema = z.enum(['rsi', 'macd_hist', 'obv', 'mfi'])

const divergenceSchema = z
  .object({
    oscillator: divergenceOscillatorSchema,
    kind: divergenceKindSchema,
    price_pivots: z.array(pivotPointSchema),
    oscillator_pivots: z.array(pivotPointSchema),
    bar_index: z.number(),
    strength: z.number(),
  })
  .strict()

export const chartDivergencesPayloadSchema = z
  .object({
    symbol: z.string().min(1),
    timeframe: z.string().min(1),
    divergences: z.array(divergenceSchema),
  })
  .strict() satisfies z.ZodType<ChartDivergencesPayloadV1>
