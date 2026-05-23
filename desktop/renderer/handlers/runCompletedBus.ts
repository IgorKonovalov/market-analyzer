/**
 * Renderer-internal pub/sub for `run.completed v1` envelopes (Plan 0008 phase 5).
 *
 * The single `useEventStream` instance lives in `App.tsx` (Plan 0007 phase 4).
 * Forwarding `run.completed` payloads from there into this module lets any
 * number of hooks subscribe to backtest / analysis / defi completions without
 * mounting a second `EventSource` or rewiring `useEventStream`'s single-handler
 * API.
 *
 * The bus is deliberately tiny and in-memory only: no buffering, no replay,
 * no per-kind routing. Subscribers do their own filtering on `payload.kind`.
 * Matches the ephemeral semantics in ADR-0017 (events not replayed across
 * disconnects).
 */
import type { RunCompletedPayloadV1 } from '../types/events'

type Listener = (payload: RunCompletedPayloadV1) => void

const listeners = new Set<Listener>()

export function subscribeRunCompleted(cb: Listener): () => void {
  listeners.add(cb)
  return () => {
    listeners.delete(cb)
  }
}

export function notifyRunCompleted(payload: RunCompletedPayloadV1): void {
  for (const cb of [...listeners]) cb(payload)
}

/** Exposed for tests that need to verify subscribe-cleanup invariants. */
export function listenerCountForTests(): number {
  return listeners.size
}
