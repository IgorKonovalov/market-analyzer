/**
 * Plan 0099 phase 4 — `defi.position_alert v1` routing at the SSE boundary:
 * a valid payload dispatches to `onDefiPositionAlert` (Zod-parsed, never a
 * bare cast); a malformed or directive-smuggling payload is dropped loudly
 * and never reaches the handler (drop-don't-render, the ADR-0029 boundary).
 */
import { dispatchEnvelope } from './useEventStream'
import type { DefiPositionAlertPayloadV1, Envelope } from '../types/events'

function payload(overrides: Partial<DefiPositionAlertPayloadV1> = {}): DefiPositionAlertPayloadV1 {
  return {
    watch_id: 7,
    wallet: '0x1234…abcd',
    chain: 'base',
    pool_address: `0x${'cd'.repeat(20)}`,
    nft_token_id: 42,
    fired_at: '2026-07-16T09:00:00Z',
    out_since: '2026-07-16T03:00:00Z',
    hours_out: 6.0,
    tick_lower: -100,
    tick_upper: 100,
    current_tick: 150,
    in_range: false,
    uncollected_fees: [{ symbol: 'USDC', amount: 1.25 }],
    ...overrides,
  }
}

function envelope(p: unknown): Envelope<unknown> {
  return { type: 'defi.position_alert', version: 1, ts: '2026-07-16T09:00:00Z', payload: p }
}

afterEach(() => {
  jest.restoreAllMocks()
})

it('dispatches a valid defi.position_alert to the handler', () => {
  const onDefiPositionAlert = jest.fn()
  dispatchEnvelope(envelope(payload()), { onDefiPositionAlert })
  expect(onDefiPositionAlert).toHaveBeenCalledTimes(1)
  expect(onDefiPositionAlert).toHaveBeenCalledWith(payload())
})

it('drops a malformed payload without calling the handler', () => {
  jest.spyOn(console, 'warn').mockImplementation(() => undefined)
  const onDefiPositionAlert = jest.fn()
  const missingHours: Partial<DefiPositionAlertPayloadV1> = { ...payload() }
  delete missingHours.hours_out
  dispatchEnvelope(envelope(missingHours), { onDefiPositionAlert })
  expect(onDefiPositionAlert).not.toHaveBeenCalled()
})

it('drops a payload smuggling a directive field (strict schema, ADR-0029)', () => {
  jest.spyOn(console, 'warn').mockImplementation(() => undefined)
  const onDefiPositionAlert = jest.fn()
  dispatchEnvelope(envelope({ ...payload(), recommendation: 'recenter' }), {
    onDefiPositionAlert,
  })
  expect(onDefiPositionAlert).not.toHaveBeenCalled()
})
