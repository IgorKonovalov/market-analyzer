/**
 * SSE consumer hook (Plan 0007 phase 4 + 4.4).
 *
 * On mount, builds the events URL from the cached `{port, secretToken}` and
 * opens an `EventSource`. Every incoming message is parsed as an
 * `Envelope<unknown>`, validated, and dispatched to a per-type handler.
 *
 * Versioning discipline (ADR-0017): an envelope with a `version` higher than
 * the handler's known version still dispatches to the v1 handler — the
 * renderer is forward-compatible by default. A `console.warn` records the
 * drift so the mismatch is visible during the transition window.
 *
 * Phase 4.4 additions:
 *   - The URL is recomputed when the api/client cache mutates (a
 *     `sidecar:status` `kind: 'refreshed'` event flowing through
 *     `subscribeToConfigChanges`). When the URL changes the previous
 *     `EventSource` is closed and a new one opens against the new URL —
 *     transparent to the agent and to the chart handlers.
 *   - On persistent connection failure (the hook's `onerror` fires 3 times
 *     within a 10-second window without an intervening `onopen`), the hook
 *     calls `window.api.sidecar.refresh()` exactly once per window. The
 *     supervisor's `refresh()` coalesces concurrent calls upstream, so the
 *     renderer is allowed to be optimistic about firing.
 *
 * Reconnection between events is left to the browser's `EventSource`
 * implementation (per ADR-0017's `retry:` hint from the server). The hook
 * surfaces the connection state (`open` | `connecting` | `reconnecting`).
 * On unmount we call `EventSource.close()`.
 *
 * The hook does NOT inject `EventSource` via a factory prop — tests install
 * a mock on `globalThis.EventSource` so production code stays free of test
 * seams.
 */
import { useEffect, useRef, useState } from 'react'

import { api, subscribeToConfigChanges } from '../api/client'
import { recommendationCompletedPayloadSchema } from '../schemas/recommendation'
import type {
  ChartHighlightPayloadV1,
  ChartShowPayloadV1,
  ChartUpdatePayloadV1,
  Envelope,
  OhlcvBackfilledPayloadV1,
  OhlcvBackfillFailedPayloadV1,
  OhlcvBackfillStartedPayloadV1,
  RecommendationCompletedPayloadV1,
  RunCompletedPayloadV1,
  SignalEvaluatedPayloadV1,
} from '../types/events'

export type ConnectionState = 'connecting' | 'open' | 'reconnecting'

export interface EventStreamHandlers {
  onChartShow?: (payload: ChartShowPayloadV1) => void
  onChartUpdate?: (payload: ChartUpdatePayloadV1) => void
  onChartHighlight?: (payload: ChartHighlightPayloadV1) => void
  onRunCompleted?: (payload: RunCompletedPayloadV1) => void
  onSignalEvaluated?: (payload: SignalEvaluatedPayloadV1) => void
  onRecommendationCompleted?: (payload: RecommendationCompletedPayloadV1) => void
  onOhlcvBackfillStarted?: (payload: OhlcvBackfillStartedPayloadV1) => void
  onOhlcvBackfilled?: (payload: OhlcvBackfilledPayloadV1) => void
  onOhlcvBackfillFailed?: (payload: OhlcvBackfillFailedPayloadV1) => void
  onUpdateDropped?: () => void
}

export interface UseEventStreamResult {
  state: ConnectionState
}

// Known per-type schema versions. When a payload arrives with a higher
// version, we still invoke the v1 handler and log a console warning — the
// renderer is forward-compatible by default per ADR-0017.
const KNOWN_VERSIONS: Record<string, number> = {
  'chart.show': 1,
  'chart.update': 1,
  'chart.highlight': 1,
  'run.completed': 1,
  'signal.evaluated': 1,
  'recommendation.completed': 1,
  'chart.update_dropped': 1,
  'ohlcv.backfill_started': 1,
  'ohlcv.backfilled': 1,
  'ohlcv.backfill_failed': 1,
}

// Phase 4.4 failure-driven recovery thresholds. 3 errors within a 10-second
// window with no intervening `onopen` is enough evidence the sidecar is
// genuinely gone (not a brief flap); the renderer asks the main process to
// re-attach. After firing, the window resets so the next refresh requires a
// fresh 3-in-10 — protects against refresh-storm if the new sidecar also
// can't be reached.
const ERROR_THRESHOLD = 3
const ERROR_WINDOW_MS = 10_000

function isEnvelope(value: unknown): value is Envelope<unknown> {
  if (typeof value !== 'object' || value === null) return false
  const v = value as Record<string, unknown>
  return (
    typeof v.type === 'string' &&
    typeof v.version === 'number' &&
    typeof v.ts === 'string' &&
    'payload' in v
  )
}

export function useEventStream(handlers: EventStreamHandlers): UseEventStreamResult {
  const [state, setState] = useState<ConnectionState>('connecting')
  const [url, setUrl] = useState<string | null>(null)

  // Keep the latest handlers on a ref so we don't have to re-open the stream
  // every time the parent re-renders with new callback identities.
  const handlersRef = useRef(handlers)
  handlersRef.current = handlers

  // Effect 1: fetch the initial URL and subscribe to cache changes. The URL
  // state is the trigger for effect 2 — recomputing it transparently re-opens
  // the EventSource against the new port + bearer.
  useEffect(() => {
    let cancelled = false

    const recompute = async (): Promise<void> => {
      try {
        const next = await api.buildEventsUrl()
        if (!cancelled) setUrl(next)
      } catch (err) {
        if (!cancelled) {
          console.warn('[useEventStream] failed to build events URL', err)
          setState('reconnecting')
        }
      }
    }

    void recompute()
    const unsubscribe = subscribeToConfigChanges(() => {
      void recompute()
    })

    return () => {
      cancelled = true
      unsubscribe()
    }
  }, [])

  // Effect 2: open / re-open the EventSource whenever the URL changes. The
  // cleanup closes the previous instance; React runs cleanup before the next
  // effect, so we never have two open at once.
  useEffect(() => {
    if (url === null) return
    const es = new EventSource(url)
    const errorTimestamps: number[] = []
    let refreshFired = false

    es.onopen = (): void => {
      setState('open')
      // Successful reconnection resets the failure window.
      errorTimestamps.length = 0
      refreshFired = false
    }
    es.onerror = (): void => {
      // EventSource fires `onerror` both on transient drops (it will then
      // reconnect itself per the server's `retry:` hint) and on fatal
      // failures (auth reject, etc.). We report `reconnecting` either way
      // — the user-visible meaning is the same: stream not currently live.
      setState('reconnecting')
      const now = Date.now()
      errorTimestamps.push(now)
      // Drop timestamps older than the rolling window.
      while (errorTimestamps.length > 0 && now - errorTimestamps[0] > ERROR_WINDOW_MS) {
        errorTimestamps.shift()
      }
      if (errorTimestamps.length >= ERROR_THRESHOLD && !refreshFired) {
        refreshFired = true
        errorTimestamps.length = 0
        void window.api.sidecar.refresh().catch((err: unknown) => {
          console.warn('[useEventStream] sidecar refresh failed', err)
        })
      }
    }
    es.onmessage = (ev: MessageEvent<string>): void => {
      let parsed: unknown
      try {
        parsed = JSON.parse(ev.data)
      } catch {
        console.warn('[useEventStream] dropping non-JSON message')
        return
      }
      if (!isEnvelope(parsed)) {
        console.warn('[useEventStream] dropping malformed envelope', parsed)
        return
      }
      dispatchEnvelope(parsed, handlersRef.current)
    }

    return () => {
      es.close()
    }
  }, [url])

  return { state }
}

/**
 * Forward-compatible dispatch: if `version` is higher than `KNOWN_VERSIONS[type]`,
 * we still invoke the v1 handler (the payload is a superset of the known shape
 * by design — ADR-0017 forbids removing fields across a minor version bump) and
 * log a warning. Exported for direct testing.
 */
export function dispatchEnvelope(envelope: Envelope<unknown>, handlers: EventStreamHandlers): void {
  const known = KNOWN_VERSIONS[envelope.type]
  if (known === undefined) {
    console.warn(`[useEventStream] dropping envelope of unknown type "${envelope.type}"`)
    return
  }
  if (envelope.version > known) {
    console.warn(
      `[useEventStream] received "${envelope.type}" v${envelope.version}; ` +
        `dispatching as v${known} (forward-compat per ADR-0017)`,
    )
  }

  switch (envelope.type) {
    case 'chart.show':
      handlers.onChartShow?.(envelope.payload as ChartShowPayloadV1)
      return
    case 'chart.update':
      handlers.onChartUpdate?.(envelope.payload as ChartUpdatePayloadV1)
      return
    case 'chart.highlight':
      handlers.onChartHighlight?.(envelope.payload as ChartHighlightPayloadV1)
      return
    case 'run.completed':
      handlers.onRunCompleted?.(envelope.payload as RunCompletedPayloadV1)
      return
    case 'signal.evaluated':
      handlers.onSignalEvaluated?.(envelope.payload as SignalEvaluatedPayloadV1)
      return
    case 'recommendation.completed': {
      // Zod-validated before it reaches any state (Plan 0039 phase 2 done-when):
      // a recommendation renders levels the user may act on outside the app, so
      // a malformed payload is dropped loudly, never rendered half-parsed.
      const parsed = recommendationCompletedPayloadSchema.safeParse(envelope.payload)
      if (!parsed.success) {
        console.warn(
          '[useEventStream] dropping malformed recommendation.completed payload',
          parsed.error.issues,
        )
        return
      }
      handlers.onRecommendationCompleted?.(parsed.data)
      return
    }
    case 'ohlcv.backfill_started':
      handlers.onOhlcvBackfillStarted?.(envelope.payload as OhlcvBackfillStartedPayloadV1)
      return
    case 'ohlcv.backfilled':
      handlers.onOhlcvBackfilled?.(envelope.payload as OhlcvBackfilledPayloadV1)
      return
    case 'ohlcv.backfill_failed':
      handlers.onOhlcvBackfillFailed?.(envelope.payload as OhlcvBackfillFailedPayloadV1)
      return
    case 'chart.update_dropped':
      handlers.onUpdateDropped?.()
      return
  }
}
