/**
 * Renderer-internal pub/sub for `alert.triggered v1` payloads (Plan 0060 phase 4).
 *
 * Mirrors `backfillBus`/`runCompletedBus`: the single `useEventStream` in
 * `App.tsx` forwards validated alert payloads here, so the toast host (mounted
 * at the App level, visible from any view) and the Alerts view's live-prepend
 * can both react without growing App state or opening a second `EventSource`.
 * Tiny, in-memory, no replay — persisted history is the sidecar's `/alerts`
 * route; this bus only carries the live session's fires.
 */
import type { AlertTriggeredPayloadV1 } from '../types/events'

type Listener = (payload: AlertTriggeredPayloadV1) => void

const listeners = new Set<Listener>()

export function subscribeAlerts(cb: Listener): () => void {
  listeners.add(cb)
  return () => {
    listeners.delete(cb)
  }
}

export function notifyAlert(payload: AlertTriggeredPayloadV1): void {
  for (const cb of [...listeners]) cb(payload)
}

/** Exposed for tests that need to verify subscribe-cleanup invariants. */
export function listenerCountForTests(): number {
  return listeners.size
}
