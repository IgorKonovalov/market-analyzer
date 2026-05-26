/**
 * Renderer-internal pub/sub for the `ohlcv.backfill_*` envelopes (Plan 0013 phase 4).
 *
 * Mirrors `runCompletedBus`: the single `useEventStream` instance in `App.tsx`
 * forwards the three backfill payloads here, so `useBackfillState` (mounted in
 * `OhlcvView`) can react without opening a second `EventSource` or growing
 * `useEventStream`'s single-handler API. Tiny, in-memory, no replay — matching
 * the ephemeral SSE semantics in ADR-0017. Subscribers filter on
 * `payload.symbol`/`payload.timeframe` themselves.
 */
import type {
  OhlcvBackfilledPayloadV1,
  OhlcvBackfillFailedPayloadV1,
  OhlcvBackfillStartedPayloadV1,
} from '../types/events'

export type BackfillEvent =
  | { kind: 'started'; payload: OhlcvBackfillStartedPayloadV1 }
  | { kind: 'backfilled'; payload: OhlcvBackfilledPayloadV1 }
  | { kind: 'failed'; payload: OhlcvBackfillFailedPayloadV1 }

type Listener = (event: BackfillEvent) => void

const listeners = new Set<Listener>()

export function subscribeBackfill(cb: Listener): () => void {
  listeners.add(cb)
  return () => {
    listeners.delete(cb)
  }
}

export function notifyBackfill(event: BackfillEvent): void {
  for (const cb of [...listeners]) cb(event)
}

/** Exposed for tests that need to verify subscribe-cleanup invariants. */
export function listenerCountForTests(): number {
  return listeners.size
}
