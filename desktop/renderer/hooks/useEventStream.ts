/**
 * SSE consumer hook (Plan 0007 phase 4).
 *
 * On mount, opens a single `EventSource` to `/events?token=<renderer_bearer>`,
 * parses every incoming message as an `Envelope<unknown>`, validates the
 * envelope shape, and dispatches to a per-type handler.
 *
 * Versioning discipline (ADR-0017): an envelope with a `version` higher than
 * the handler's known version still dispatches to the v1 handler — the renderer
 * is forward-compatible by default. A `console.warn` records the drift so the
 * mismatch is visible during the transition window.
 *
 * Reconnection is left to the browser's `EventSource` implementation; we surface
 * the connection state (`open` | `connecting` | `reconnecting`) so the UI can
 * indicate stream health if it wants. On unmount we call `EventSource.close()`.
 *
 * The hook does NOT inject `EventSource` via a factory prop — tests install a
 * mock on `globalThis.EventSource` so production code stays free of test seams.
 */
import { useEffect, useRef, useState } from 'react'

import { api } from '../api/client'
import type {
  ChartHighlightPayloadV1,
  ChartShowPayloadV1,
  ChartUpdatePayloadV1,
  Envelope,
  RunCompletedPayloadV1,
} from '../types/events'

export type ConnectionState = 'connecting' | 'open' | 'reconnecting'

export interface EventStreamHandlers {
  onChartShow?: (payload: ChartShowPayloadV1) => void
  onChartUpdate?: (payload: ChartUpdatePayloadV1) => void
  onChartHighlight?: (payload: ChartHighlightPayloadV1) => void
  onRunCompleted?: (payload: RunCompletedPayloadV1) => void
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
  'chart.update_dropped': 1,
}

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

  // Keep the latest handlers on a ref so we don't have to re-open the stream
  // every time the parent re-renders with new callback identities.
  const handlersRef = useRef(handlers)
  handlersRef.current = handlers

  useEffect(() => {
    let cancelled = false
    let es: EventSource | null = null

    api
      .buildEventsUrl()
      .then((url) => {
        if (cancelled) return
        es = new EventSource(url)

        es.onopen = (): void => {
          setState('open')
        }
        es.onerror = (): void => {
          // EventSource fires `onerror` both on transient drops (it will then
          // reconnect itself per the server's `retry:` hint) and on fatal
          // failures (auth reject, etc.). We report `reconnecting` either way
          // — the user-visible meaning is the same: stream not currently live.
          setState('reconnecting')
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
      })
      .catch((err: unknown) => {
        if (cancelled) return
        console.warn('[useEventStream] failed to build events URL', err)
        setState('reconnecting')
      })

    return () => {
      cancelled = true
      es?.close()
    }
  }, [])

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
    case 'chart.update_dropped':
      handlers.onUpdateDropped?.()
      return
  }
}
