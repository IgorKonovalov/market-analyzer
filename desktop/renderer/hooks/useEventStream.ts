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
 * Reconnection re-mints (ADR-0066): the stream authenticates with a
 * short-lived, single-use ticket in the URL, so the browser's native
 * `EventSource` auto-reconnect — which replays the same (now-consumed) URL —
 * would 401-loop forever. The hook therefore closes the `EventSource` on
 * `onerror` and schedules its own reconnect, which mints a FRESH ticketed URL
 * (`api.buildEventsUrl()`) before reopening. The hook surfaces the connection
 * state (`open` | `connecting` | `reconnecting`) and closes the stream (and
 * cancels any pending reconnect) on unmount.
 *
 * The hook does NOT inject `EventSource` via a factory prop — tests install
 * a mock on `globalThis.EventSource` so production code stays free of test
 * seams.
 */
import { useEffect, useRef, useState } from 'react'

import { api, subscribeToConfigChanges } from '../api/client'
import { parseAlertTriggered } from '../schemas/alertTriggered'
import { forecastCompletedPayloadSchema } from '../schemas/forecastCompleted'
import { recommendationCompletedPayloadSchema } from '../schemas/recommendation'
import { recommendationScoredPayloadSchema } from '../schemas/recommendationScored'
import { regimeForecastCompletedPayloadSchema } from '../schemas/regimeForecastCompleted'
import { technicalReadCompletedPayloadSchema } from '../schemas/technicalReadCompleted'
import { volatilityForecastCompletedPayloadSchema } from '../schemas/volatilityForecastCompleted'
import type {
  AlertTriggeredPayloadV1,
  ChartHighlightPayloadV1,
  ChartShowPayloadV1,
  ChartTrendlinesPayloadV1,
  ChartUpdatePayloadV1,
  Envelope,
  ForecastCompletedPayloadV1,
  OhlcvBackfilledPayloadV1,
  OhlcvBackfillFailedPayloadV1,
  OhlcvBackfillStartedPayloadV1,
  RecommendationCompletedPayloadV1,
  RecommendationScoredPayloadV1,
  RegimeForecastCompletedPayloadV1,
  RunCompletedPayloadV1,
  SignalEvaluatedPayloadV1,
  TechnicalReadCompletedPayloadV1,
  VolatilityForecastCompletedPayloadV1,
} from '../types/events'

export type ConnectionState = 'connecting' | 'open' | 'reconnecting'

export interface EventStreamHandlers {
  onChartShow?: (payload: ChartShowPayloadV1) => void
  onChartUpdate?: (payload: ChartUpdatePayloadV1) => void
  onChartHighlight?: (payload: ChartHighlightPayloadV1) => void
  onChartTrendlines?: (payload: ChartTrendlinesPayloadV1) => void
  onRunCompleted?: (payload: RunCompletedPayloadV1) => void
  onSignalEvaluated?: (payload: SignalEvaluatedPayloadV1) => void
  onRecommendationCompleted?: (payload: RecommendationCompletedPayloadV1) => void
  onRecommendationScored?: (payload: RecommendationScoredPayloadV1) => void
  onForecastCompleted?: (payload: ForecastCompletedPayloadV1) => void
  onVolatilityForecastCompleted?: (payload: VolatilityForecastCompletedPayloadV1) => void
  onRegimeForecastCompleted?: (payload: RegimeForecastCompletedPayloadV1) => void
  onTechnicalReadCompleted?: (payload: TechnicalReadCompletedPayloadV1) => void
  onOhlcvBackfillStarted?: (payload: OhlcvBackfillStartedPayloadV1) => void
  onOhlcvBackfilled?: (payload: OhlcvBackfilledPayloadV1) => void
  onOhlcvBackfillFailed?: (payload: OhlcvBackfillFailedPayloadV1) => void
  onUpdateDropped?: () => void
  onAlertTriggered?: (payload: AlertTriggeredPayloadV1) => void
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
  'chart.trendlines': 1,
  'run.completed': 1,
  'signal.evaluated': 1,
  'recommendation.completed': 1,
  'recommendation.scored': 1,
  'forecast.completed': 1,
  'volatility_forecast.completed': 1,
  'regime_forecast.completed': 1,
  'technical_read.completed': 1,
  'chart.update_dropped': 1,
  'ohlcv.backfill_started': 1,
  'ohlcv.backfilled': 1,
  'ohlcv.backfill_failed': 1,
  'alert.triggered': 1,
}

// Phase 4.4 failure-driven recovery thresholds. 3 errors within a 10-second
// window with no intervening `onopen` is enough evidence the sidecar is
// genuinely gone (not a brief flap); the renderer asks the main process to
// re-attach. After firing, the window resets so the next refresh requires a
// fresh 3-in-10 — protects against refresh-storm if the new sidecar also
// can't be reached.
const ERROR_THRESHOLD = 3
const ERROR_WINDOW_MS = 10_000

// Delay before a manual reconnect attempt. Native `EventSource` auto-reconnect
// is disabled (a single-use ticket can't be replayed — ADR-0066), so the hook
// re-mints and reopens itself after this backoff, in the spirit of the server's
// `retry:` hint.
const RECONNECT_DELAY_MS = 3000

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

  // Effect 2: open the EventSource for the current URL, then own reconnection
  // ourselves. A single-use ticket (ADR-0066) is consumed on open, so native
  // auto-reconnect (same URL) is useless — on error we close and schedule a
  // reconnect that mints a FRESH ticketed URL. The cleanup closes the current
  // instance and cancels any pending reconnect; React runs cleanup before the
  // next effect, so a URL change (config refresh) never leaves two open.
  useEffect(() => {
    if (url === null) return

    let es: EventSource | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let disposed = false
    const errorTimestamps: number[] = []
    let refreshFired = false

    const recordFailure = (): void => {
      const now = Date.now()
      errorTimestamps.push(now)
      // Drop timestamps older than the rolling window.
      while (errorTimestamps.length > 0 && now - errorTimestamps[0] > ERROR_WINDOW_MS) {
        errorTimestamps.shift()
      }
      // 3 failures within the window with no intervening `onopen` is enough
      // evidence the sidecar is genuinely gone — ask the main process to
      // re-attach, once per window (the supervisor coalesces upstream).
      if (errorTimestamps.length >= ERROR_THRESHOLD && !refreshFired) {
        refreshFired = true
        errorTimestamps.length = 0
        void window.api.sidecar.refresh().catch((err: unknown) => {
          console.warn('[useEventStream] sidecar refresh failed', err)
        })
      }
    }

    const scheduleReconnect = (): void => {
      if (disposed || reconnectTimer !== null) return
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null
        void reconnect()
      }, RECONNECT_DELAY_MS)
    }

    const reconnect = async (): Promise<void> => {
      if (disposed) return
      let fresh: string
      try {
        fresh = await api.buildEventsUrl() // mints a fresh single-use ticket
      } catch (err) {
        console.warn('[useEventStream] failed to mint SSE ticket for reconnect', err)
        recordFailure()
        scheduleReconnect()
        return
      }
      if (!disposed) openStream(fresh)
    }

    const openStream = (streamUrl: string): void => {
      es = new EventSource(streamUrl)
      es.onopen = (): void => {
        setState('open')
        // Successful (re)connection resets the failure window.
        errorTimestamps.length = 0
        refreshFired = false
      }
      es.onerror = (): void => {
        // `onerror` fires on both transient drops and fatal failures; either
        // way the ticket in this URL is spent, so we don't let the browser
        // retry it. Report `reconnecting`, close, and re-mint on a backoff.
        setState('reconnecting')
        recordFailure()
        es?.close()
        es = null
        scheduleReconnect()
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
    }

    openStream(url)

    return () => {
      disposed = true
      if (reconnectTimer !== null) clearTimeout(reconnectTimer)
      es?.close()
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
    case 'chart.trendlines':
      handlers.onChartTrendlines?.(envelope.payload as ChartTrendlinesPayloadV1)
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
    case 'recommendation.scored': {
      // Zod-validated before it reaches any state (Plan 0080 phase 5): the
      // track-record panel refetches the authoritative aggregate on this nudge,
      // so a malformed payload is dropped loudly, never acted on half-parsed.
      const parsed = recommendationScoredPayloadSchema.safeParse(envelope.payload)
      if (!parsed.success) {
        console.warn(
          '[useEventStream] dropping malformed recommendation.scored payload',
          parsed.error.issues,
        )
        return
      }
      handlers.onRecommendationScored?.(parsed.data)
      return
    }
    case 'forecast.completed': {
      // Zod-validated before it reaches any state (Plan 0037 phase 2 done-when):
      // a probability is the most over-trusted output the app produces
      // (ADR-0030), so a malformed payload is dropped loudly, never rendered
      // half-parsed as a confident number.
      const parsed = forecastCompletedPayloadSchema.safeParse(envelope.payload)
      if (!parsed.success) {
        console.warn(
          '[useEventStream] dropping malformed forecast.completed payload',
          parsed.error.issues,
        )
        return
      }
      handlers.onForecastCompleted?.(parsed.data)
      return
    }
    case 'volatility_forecast.completed': {
      // Zod-validated before it reaches any state (Plan 0077 phase 6): a
      // volatility magnitude read as a number is dropped loudly when malformed,
      // never rendered half-parsed (the honest-uncertainty posture, ADR-0070).
      const parsed = volatilityForecastCompletedPayloadSchema.safeParse(envelope.payload)
      if (!parsed.success) {
        console.warn(
          '[useEventStream] dropping malformed volatility_forecast.completed payload',
          parsed.error.issues,
        )
        return
      }
      handlers.onVolatilityForecastCompleted?.(parsed.data)
      return
    }
    case 'regime_forecast.completed': {
      // Zod-validated before it reaches any state (Plan 0077 phase 6): a
      // transition distribution is dropped loudly when malformed, never rendered
      // half-parsed as a confident regime call (ADR-0070).
      const parsed = regimeForecastCompletedPayloadSchema.safeParse(envelope.payload)
      if (!parsed.success) {
        console.warn(
          '[useEventStream] dropping malformed regime_forecast.completed payload',
          parsed.error.issues,
        )
        return
      }
      handlers.onRegimeForecastCompleted?.(parsed.data)
      return
    }
    case 'technical_read.completed': {
      // Zod-validated before it reaches any state (Plan 0074 phase 3): the lesser
      // advisory tier still emits a direction a user might act on, so a malformed
      // payload is dropped loudly, never rendered half-parsed (ADR-0068).
      const parsed = technicalReadCompletedPayloadSchema.safeParse(envelope.payload)
      if (!parsed.success) {
        console.warn(
          '[useEventStream] dropping malformed technical_read.completed payload',
          parsed.error.issues,
        )
        return
      }
      handlers.onTechnicalReadCompleted?.(parsed.data)
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
    case 'alert.triggered': {
      // Zod-validated at the boundary (Plan 0060 phase 4, the standing
      // SSE-validation follow-up): a malformed payload is dropped with a
      // logged warning inside `parseAlertTriggered`, never rendered.
      const alert = parseAlertTriggered(envelope.payload)
      if (alert !== null) handlers.onAlertTriggered?.(alert)
      return
    }
    case 'chart.update_dropped':
      handlers.onUpdateDropped?.()
      return
  }
}
