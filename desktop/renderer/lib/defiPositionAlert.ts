/**
 * Display helpers for `defi.position_alert v1` (Plan 0099 phase 4). One
 * formatter feeds all three surfaces — in-app toast, OS-notification body,
 * and the Alerts view rows — so the wording stays a condition FACT
 * everywhere (ADR-0029: never advice).
 */
import type { DefiPositionAlertPayloadV1 } from '../types/events'

/** `0xcdcdcd…cdcd` → `0xcdcd…cdcd` — the sidecar's mask shape, applied to
 * pool addresses (wallets already arrive masked). Short strings pass through. */
export function shortAddress(address: string): string {
  if (address.length <= 11) return address
  return `${address.slice(0, 6)}…${address.slice(-4)}`
}

/** The condition fact in one line, e.g.
 * `LP out of range 6.2h — base pool 0xcdcd…cdcd, tick 150 outside [-100, 100)`. */
export function defiAlertMessage(payload: DefiPositionAlertPayloadV1): string {
  return (
    `LP out of range ${payload.hours_out.toFixed(1)}h — ${payload.chain} pool ` +
    `${shortAddress(payload.pool_address)}, tick ${payload.current_tick} outside ` +
    `[${payload.tick_lower}, ${payload.tick_upper})`
  )
}
