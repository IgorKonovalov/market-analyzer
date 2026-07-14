/**
 * Zod schema for the `chart.annotations v1` SSE payload (Plan 0097 phase 4,
 * ADR-0091).
 *
 * Validated at runtime in the dispatcher before it reaches any chart state (the
 * `chart.divergences` precedent): an agent drawing carries geometry the renderer
 * strokes and the user may act on, so a malformed payload is dropped loudly with
 * a `console.warn` rather than half-drawn. `.strict()` mirrors the pydantic
 * `extra="forbid"` on every level. Per-symbol — there is deliberately NO
 * `timeframe` field (a drawing renders across every timeframe, ADR-0091).
 *
 * The schema is `satisfies`-pinned to the hand-written TS mirror in
 * `types/events.ts`, whose parity against the pydantic source of truth is guarded
 * by `types/events.test.ts`.
 */
import { z } from 'zod'

import type { ChartAnnotationsPayloadV1, DrawingSpec } from '../types/events'

const timePricePointSchema = z
  .object({
    ts: z.string(),
    price: z.number(),
  })
  .strict()

const drawingStyleSchema = z
  .object({
    color: z.string().nullish(),
    width: z.number().nullish(),
  })
  .strict()

const drawingKindSchema = z.enum(['trendline', 'ray', 'hline', 'vline', 'rect', 'fib'])

const drawingSpecSchema = z
  .object({
    kind: drawingKindSchema,
    points: z.array(timePricePointSchema),
    provenance: z.enum(['agent', 'user']),
    style: drawingStyleSchema.nullish(),
    id: z.string().min(1),
  })
  .strict() satisfies z.ZodType<DrawingSpec>

export const chartAnnotationsPayloadSchema = z
  .object({
    symbol: z.string().min(1),
    drawings: z.array(drawingSpecSchema),
  })
  .strict() satisfies z.ZodType<ChartAnnotationsPayloadV1>
