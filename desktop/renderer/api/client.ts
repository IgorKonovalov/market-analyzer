/**
 * Renderer-side fetch wrapper. Reads `{port, secretToken}` from the preload
 * bridge on first call and injects `Authorization: Bearer <secret>` plus the
 * sidecar base URL. The secret is held in a module-level closure — never
 * passed back across IPC, never written to disk, never logged.
 *
 * On `sidecar:status` events the cached `{port, secretToken}` is updated in
 * place so subsequent calls use the freshly-rotated values:
 *   - `kind === 'restarted'` — legacy event (pre-ADR-0016 crash-supervised
 *     restart). The supervisor no longer emits this kind, but the branch
 *     stays as a no-op-tolerant fallback so a stale main-process binary on
 *     a user machine can't break the renderer's cache.
 *   - `kind === 'refreshed'` — Plan 0007 phase 4.3 event from
 *     `SidecarSupervisor.refresh()`. BOTH `port` and `secretToken` move
 *     atomically because a standalone-mode sidecar that restarted
 *     out-of-band can be on a new port (per ADR-0016).
 *
 * `subscribeToConfigChanges(cb)` lets the SSE consumer (`useEventStream`) be
 * notified synchronously when the cache mutates so it can re-open its
 * `EventSource` against the new URL. The subscription is set up at module
 * load before any `sidecarFetch` call so a fast restart cannot lose the
 * event.
 */
import type { CandlestickData, UTCTimestamp } from 'lightweight-charts'

import type { SidecarPort } from '../../shared/schemas/sidecar'
import type { Annotation } from '../types/sidecar/annotation'
import type { Bar } from '../types/sidecar/bar'
import type { McpSecretRecord } from '../types/sidecar/mcp-secret-record'
import type { NewsResponse } from '../types/sidecar/news-response'
import type { QuoteResponse } from '../types/sidecar/quote-response'
import type { ScanChartPatternsRequest } from '../types/sidecar/scan-chart-patterns-request'
import type { ScanChartPatternsResponse } from '../types/sidecar/scan-chart-patterns-response'
import type { ScanPatternsRequest } from '../types/sidecar/scan-patterns-request'
import type { ScanPatternsResponse } from '../types/sidecar/scan-patterns-response'
import type { SymbolInfo } from '../types/sidecar/symbol-info'
import type { AgentModeState } from '../types/ui-events'
import type { AlertsPage } from '../types/sidecar/alerts-page'
import type { WatchOut } from '../types/sidecar/watch-out'

let cached: SidecarPort | null = null
const configChangeSubscribers = new Set<() => void>()

function notifyConfigChange(): void {
  for (const cb of configChangeSubscribers) cb()
}

/**
 * Subscribe to cache-mutation events. The callback fires synchronously on
 * every update to `{port, secretToken}` triggered by a `sidecar:status`
 * event. Returns an unsubscribe function.
 */
export function subscribeToConfigChanges(cb: () => void): () => void {
  configChangeSubscribers.add(cb)
  return () => {
    configChangeSubscribers.delete(cb)
  }
}

// The `onStatus` reference we last subscribed against. In production the
// preload's `window.api.sidecar.onStatus` is stable and we register once at
// module load. In tests the mock is replaced between cases, so we re-check
// the identity on every `getSidecarConfig` call and re-register if it moved.
// The identity guard keeps production from accumulating duplicate listeners.
let registeredOnStatus: unknown = null

function ensureStatusListener(): void {
  if (typeof window === 'undefined') return
  const onStatus = window.api?.sidecar?.onStatus
  if (onStatus === undefined || onStatus === registeredOnStatus) return
  onStatus((status) => {
    if (status.kind === 'restarted' && status.secretToken && cached) {
      cached = { ...cached, secretToken: status.secretToken }
      notifyConfigChange()
      return
    }
    if (status.kind === 'refreshed' && cached) {
      // SidecarStatusSchema enforces both fields when kind === 'refreshed';
      // the non-null assertions are TS-only — at runtime the supervisor's
      // emit path always populates them (validated in main-process tests).
      cached = { port: status.port!, secretToken: status.secretToken! }
      notifyConfigChange()
    }
  })
  registeredOnStatus = onStatus
}

// Try eagerly at module load (production preload is ready by then). The
// `getSidecarConfig` path below retries on every call so tests that set
// `window.api` AFTER module load still get a registered listener.
ensureStatusListener()

async function getSidecarConfig(): Promise<SidecarPort> {
  ensureStatusListener()
  if (cached) return cached
  cached = await window.api.sidecar.getPort()
  return cached
}

export async function sidecarFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const { port, secretToken } = await getSidecarConfig()
  const url = `http://127.0.0.1:${port}${path}`
  const headers = new Headers(init.headers)
  if (path !== '/healthz') {
    headers.set('Authorization', `Bearer ${secretToken}`)
  }
  return fetch(url, { ...init, headers })
}

export class ApiError extends Error {
  readonly status: number
  readonly body: string
  constructor(status: number, body: string) {
    super(`sidecar ${status}: ${sanitizeApiErrorBody(body)}`)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

const MAX_MESSAGE_LENGTH = 280
const WINDOWS_PATH_RE = /[A-Za-z]:[\\/](?:[^\s'"`<>|*?]+[\\/])*[^\s'"`<>|*?]*/g
const POSIX_PATH_RE = /\/(?:Users|home|var|tmp|opt|etc|root|srv|mnt|usr|private)\/[^\s'"`<>|]+/g

/**
 * Public for tests. Reduces a raw sidecar error body to something safe to
 * render in the DOM: pulls out FastAPI's `{detail}` if present, masks
 * absolute filesystem paths, drops Python traceback frames, and clamps to
 * a max length so a 4 KB stack dump doesn't blow out a toast.
 *
 * The raw body stays on `ApiError.body` for logging.
 */
export function sanitizeApiErrorBody(body: string): string {
  if (!body) return '(empty body)'

  let text = body
  try {
    const parsed = JSON.parse(body) as unknown
    if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
      const detail = (parsed as { detail: unknown }).detail
      if (typeof detail === 'string' && detail.length > 0) {
        text = detail
      }
    }
  } catch {
    // not JSON — fall through to string sanitization
  }

  text = text
    .split(/\r?\n/)
    .filter((line) => {
      const trimmed = line.trim()
      if (trimmed.startsWith('Traceback (most recent call last)')) return false
      if (trimmed.startsWith('File "')) return false
      return true
    })
    .join(' ')
    .replace(WINDOWS_PATH_RE, '<path>')
    .replace(POSIX_PATH_RE, '<path>')
    .replace(/\s+/g, ' ')
    .trim()

  if (text.length > MAX_MESSAGE_LENGTH) {
    text = `${text.slice(0, MAX_MESSAGE_LENGTH - 1)}…`
  }

  return text || '(empty body)'
}

export async function callJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await sidecarFetch(path, init)
  if (!res.ok) {
    throw new ApiError(res.status, await res.text())
  }
  return (await res.json()) as T
}

export interface GetOhlcvParams {
  symbol: string
  timeframe: string
  start: Date
  end: Date
}

export type GetAnnotationsParams = GetOhlcvParams

export type NewsWindow = '1h' | '4h' | '24h' | '7d'

export interface GetNewsParams {
  /** Blank/omitted → browse all feeds (the response then has `sentiment: null`). */
  symbol?: string
  window: NewsWindow
  limit?: number
}

export const api = {
  getOhlcv({ symbol, timeframe, start, end }: GetOhlcvParams): Promise<Bar[]> {
    const params = new URLSearchParams({
      symbol,
      timeframe,
      start: start.toISOString(),
      end: end.toISOString(),
    })
    return callJson<Bar[]>(`/ohlcv?${params.toString()}`)
  },
  getAnnotations({ symbol, timeframe, start, end }: GetAnnotationsParams): Promise<Annotation[]> {
    const params = new URLSearchParams({
      symbol,
      timeframe,
      start: start.toISOString(),
      end: end.toISOString(),
    })
    return callJson<Annotation[]>(`/annotations?${params.toString()}`)
  },
  /**
   * Symbol search for the autocomplete dropdown (Plan 0024). Hits the
   * renderer-bearer-gated `GET /search?q=`; every returned `symbol` is in the
   * Yahoo OHLCV namespace, so a picked row is directly fetchable by `getOhlcv`
   * (ADR-0026). An empty/whitespace query returns `[]` from the sidecar.
   */
  searchSymbols(query: string): Promise<SymbolInfo[]> {
    const params = new URLSearchParams({ q: query })
    return callJson<SymbolInfo[]>(`/search?${params.toString()}`)
  },
  /**
   * Live, symbol-level quote for the price header (Plan 0047). Hits the
   * renderer-bearer-gated `GET /quote?symbol=`. Timeframe-independent — the
   * header polls this so the displayed current price tracks the live quote,
   * not the selected OHLCV series' last bar. `change_pct` is a percentage
   * (already ×100 upstream) and may be `null`.
   */
  getQuote(symbol: string): Promise<QuoteResponse> {
    const params = new URLSearchParams({ symbol })
    return callJson<QuoteResponse>(`/quote?${params.toString()}`)
  },
  /**
   * Sweep the chart's current visible range for candlestick patterns (Plan 0049).
   * Hits the renderer-bearer-gated `POST /scan_patterns`; the markers themselves
   * arrive on the `/events` SSE stream (this returns only the `{published, count}`
   * ack — no second draw path). Same pure core as the `scan_patterns` MCP tool, so
   * the UI and agent triggers emit identical markers (ADR-0045).
   */
  scanPatterns(req: ScanPatternsRequest): Promise<ScanPatternsResponse> {
    return callJson<ScanPatternsResponse>('/scan_patterns', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    })
  },
  /**
   * Sweep the chart's current visible range for classical chart PATTERNS (necklines,
   * triangle/wedge bounds) — the trendline sibling of `scanPatterns` (Plan 0064,
   * ADR-0059). Hits the renderer-bearer-gated `POST /scan_chart_patterns`; the
   * trendlines arrive on the `/events` SSE stream as a `chart.trendlines` event
   * (this returns only the `{published, count}` ack). Same pure core as the
   * `detect_chart_patterns` MCP tool, so the UI recompute and agent triggers emit
   * identical geometry. The renderer fires this on chart load / range change so the
   * lines track the bars on screen (derived, never persisted).
   */
  scanChartPatterns(req: ScanChartPatternsRequest): Promise<ScanChartPatternsResponse> {
    return callJson<ScanChartPatternsResponse>('/scan_chart_patterns', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    })
  },
  /** Read the persisted agent-mode toggle (Plan 0014). Renderer-bearer-gated. */
  getAgentMode(): Promise<AgentModeState> {
    return callJson<AgentModeState>('/agent_mode')
  },
  /** Persist the agent-mode toggle. Returns the new state. */
  setAgentMode(enabled: boolean): Promise<AgentModeState> {
    return callJson<AgentModeState>('/agent_mode', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
  },
  /**
   * Recent headlines + aggregate tone (Plan 0023). Hits the renderer-bearer-
   * gated `GET /news`. A blank/omitted `symbol` browses all feeds and the
   * response carries `sentiment: null` (no per-symbol aggregate). Feed content
   * is untrusted — the view renders it as text and sanitizes hrefs (ADR-0008).
   */
  getNews({ symbol, window, limit }: GetNewsParams): Promise<NewsResponse> {
    const params = new URLSearchParams({ window })
    const trimmed = symbol?.trim()
    if (trimmed) params.set('symbol', trimmed)
    if (limit !== undefined) params.set('limit', String(limit))
    return callJson<NewsResponse>(`/news?${params.toString()}`)
  },
  /**
   * The persisted watch definitions (Plan 0060). Renderer-bearer-gated
   * `GET /watches`. Agent creates via MCP; the viewer only lists + toggles.
   */
  getWatches(): Promise<WatchOut[]> {
    return callJson<WatchOut[]>('/watches')
  },
  /** Enable/disable one watch — the single viewer-owned mutation (Plan 0060). */
  setWatchEnabled(watchId: number, enabled: boolean): Promise<WatchOut> {
    return callJson<WatchOut>(`/watches/${watchId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
  },
  /**
   * Newest-first fired-alert history (Plan 0060). Renderer-bearer-gated
   * `GET /alerts`; each row's `payload` is the condition-only
   * `alert.triggered v1` fact.
   */
  getAlerts(
    params: { watchId?: number; offset?: number; limit?: number } = {},
  ): Promise<AlertsPage> {
    const search = new URLSearchParams()
    if (params.watchId !== undefined) search.set('watch_id', String(params.watchId))
    if (params.offset !== undefined) search.set('offset', String(params.offset))
    if (params.limit !== undefined) search.set('limit', String(params.limit))
    const query = search.toString()
    return callJson<AlertsPage>(query ? `/alerts?${query}` : '/alerts')
  },
  getMcpSecret(): Promise<McpSecretRecord> {
    return callJson<McpSecretRecord>('/settings/mcp-secret')
  },
  rotateMcpSecret(): Promise<McpSecretRecord> {
    return callJson<McpSecretRecord>('/settings/mcp-secret/rotate', { method: 'POST' })
  },
  /** Schedule a graceful sidecar shutdown (ADR-0016, Plan 0007). The sidecar
   * runs its `finally` block: removes `sidecar.lock`, then exits. */
  stopSidecar(): Promise<{ stopping: boolean }> {
    return callJson<{ stopping: boolean }>('/settings/stop', { method: 'POST' })
  },
  /** Exposed so the Settings page can render `http://127.0.0.1:<port>/mcp` for copy-paste. */
  getSidecarPort(): Promise<number> {
    return getSidecarConfig().then((c) => c.port)
  },
  /**
   * Build the SSE stream URL for `useEventStream` to hand to `new EventSource(...)`.
   * The renderer bearer must be passed as `?token=` because `EventSource` cannot
   * send custom `Authorization` headers (ADR-0017). The query path is only
   * accepted on `/events` by the sidecar and the access log is suppressed.
   */
  buildEventsUrl(): Promise<string> {
    return getSidecarConfig().then(
      ({ port, secretToken }) =>
        `http://127.0.0.1:${port}/events?token=${encodeURIComponent(secretToken)}`,
    )
  },
} as const

export function toLightweightBar(b: Bar): CandlestickData {
  return {
    time: Math.floor(new Date(b.event_ts).getTime() / 1000) as UTCTimestamp,
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
  }
}
