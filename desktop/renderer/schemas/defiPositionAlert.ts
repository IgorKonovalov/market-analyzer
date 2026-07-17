/**
 * Zod schema for the `defi.position_alert v1` SSE payload (Plan 0099 phase 4,
 * ADR-0093/0094).
 *
 * Parsed at the SSE boundary like `alert.triggered` — a malformed payload is
 * dropped with a logged warning, never rendered and never forwarded to the
 * OS-notification bridge. `strict()` mirrors the pydantic model's
 * `extra="forbid"`, so a payload smuggling directive-shaped fields past the
 * ADR-0029 boundary fails validation rather than riding along.
 *
 * Keep in sync with `DefiPositionAlertPayloadV1` in `../types/events.ts`
 * (the hand-written mirror); the compile-time drift guard below keeps the
 * two from drifting silently.
 */
import { z } from 'zod'

import type { DefiPositionAlertPayloadV1 } from '../types/events'

export const DefiPositionAlertPayloadSchema = z
  .object({
    watch_id: z.number().int(),
    wallet: z.string().min(1),
    chain: z.string().min(1),
    pool_address: z.string().min(1),
    nft_token_id: z.number().int().nullable(),
    fired_at: z.string().min(1),
    out_since: z.string().min(1),
    hours_out: z.number(),
    tick_lower: z.number().int(),
    tick_upper: z.number().int(),
    current_tick: z.number().int(),
    in_range: z.boolean(),
    uncollected_fees: z
      .array(z.object({ symbol: z.string(), amount: z.number() }).strict())
      .nullable(),
  })
  .strict()

// Compile-time drift guard: the schema's output must be assignable to the
// hand-written mirror and vice versa. If either side changes shape, this
// stops typechecking.
type SchemaOutput = z.infer<typeof DefiPositionAlertPayloadSchema>
const _schemaMatchesMirror: SchemaOutput extends DefiPositionAlertPayloadV1
  ? DefiPositionAlertPayloadV1 extends SchemaOutput
    ? true
    : never
  : never = true
void _schemaMatchesMirror

/**
 * Parse an unknown SSE payload into a typed DeFi position alert, or `null`
 * (with a logged warning) when it fails validation — the drop-don't-render
 * contract.
 */
export function parseDefiPositionAlert(payload: unknown): DefiPositionAlertPayloadV1 | null {
  const result = DefiPositionAlertPayloadSchema.safeParse(payload)
  if (!result.success) {
    console.warn(
      '[defiPositionAlert] dropping malformed defi.position_alert payload',
      result.error.issues,
    )
    return null
  }
  return result.data
}
