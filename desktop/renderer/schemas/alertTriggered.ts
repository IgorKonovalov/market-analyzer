/**
 * Zod schema for the `alert.triggered v1` SSE payload (Plan 0060 phase 4).
 *
 * The standing SSE-validation follow-up starts here: unlike the older event
 * mirrors (trusted `as`-casts in `dispatchEnvelope`), the alert payload is
 * parsed at the boundary — a malformed payload is dropped with a logged
 * warning, never rendered. `strict()` mirrors the pydantic model's
 * `extra="forbid"`, so a payload smuggling recommendation-shaped fields past
 * the ADR-0029 boundary fails validation rather than riding along.
 *
 * Keep in sync with `AlertTriggeredPayloadV1` in `../types/events.ts` (the
 * hand-written mirror whose parity test pins the pydantic shape); the
 * `satisfies`-style check below keeps the two from drifting silently.
 */
import { z } from 'zod'

import type { AlertTriggeredPayloadV1 } from '../types/events'

export const AlertTriggeredPayloadSchema = z
  .object({
    watch_id: z.number().int(),
    symbol: z.string().min(1),
    timeframe: z.string().min(1),
    kind: z.enum(['indicator_threshold', 'pattern', 'strategy_signal']),
    fired_at: z.string().min(1),
    condition: z.string(),
    values: z.record(z.number()),
  })
  .strict()

// Compile-time drift guard: the schema's output must be assignable to the
// hand-written mirror and vice versa. If either side changes shape, this
// stops typechecking.
type SchemaOutput = z.infer<typeof AlertTriggeredPayloadSchema>
const _schemaMatchesMirror: SchemaOutput extends AlertTriggeredPayloadV1
  ? AlertTriggeredPayloadV1 extends SchemaOutput
    ? true
    : never
  : never = true
void _schemaMatchesMirror

/**
 * Parse an unknown SSE payload into a typed alert, or `null` (with a logged
 * warning) when it fails validation — the drop-don't-render contract.
 */
export function parseAlertTriggered(payload: unknown): AlertTriggeredPayloadV1 | null {
  const result = AlertTriggeredPayloadSchema.safeParse(payload)
  if (!result.success) {
    console.warn('[alertTriggered] dropping malformed alert.triggered payload', result.error.issues)
    return null
  }
  return result.data
}
