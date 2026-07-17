/**
 * Renderer-internal pub/sub for `defi.position_alert v1` payloads (Plan 0099
 * phase 4) — the DeFi sibling of `alertBus`, same shape for the same reason:
 * the single `useEventStream` in `App.tsx` forwards validated payloads here,
 * so the toast host (any view) and the Alerts view's DeFi panel can both
 * react without growing App state. Tiny, in-memory, no replay — persisted
 * history is the sidecar's `/defi/position_alerts` route; this bus only
 * carries the live session's fires.
 */
import type { DefiPositionAlertPayloadV1 } from '../types/events'

type Listener = (payload: DefiPositionAlertPayloadV1) => void

const listeners = new Set<Listener>()

export function subscribeDefiPositionAlerts(cb: Listener): () => void {
  listeners.add(cb)
  return () => {
    listeners.delete(cb)
  }
}

export function notifyDefiPositionAlert(payload: DefiPositionAlertPayloadV1): void {
  for (const cb of [...listeners]) cb(payload)
}

/** Exposed for tests that need to verify subscribe-cleanup invariants. */
export function defiListenerCountForTests(): number {
  return listeners.size
}
