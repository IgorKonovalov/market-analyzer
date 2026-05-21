/**
 * Renderer-side fetch wrapper. Reads `{port, secretToken}` from the preload
 * bridge on first call and injects `Authorization: Bearer <secret>` plus the
 * sidecar base URL. The secret is held in a module-level closure — never
 * passed back across IPC, never written to disk, never logged.
 *
 * On `sidecar:status` events with `kind === 'restarted'`, the cached port is
 * updated in place so subsequent calls use the freshly-rotated bearer secret
 * (the supervisor rotates per restart per ADR-0002). The subscription is set up
 * at module load before any `sidecarFetch` call so a fast restart cannot lose
 * the event.
 */
import type { CandlestickData, UTCTimestamp } from 'lightweight-charts'

import type { SidecarPort } from '../../shared/schemas/sidecar'
import type { Annotation } from '../types/sidecar/annotation'
import type { Bar } from '../types/sidecar/bar'
import type { McpSecretRecord } from '../types/sidecar/mcp-secret-record'

let cached: SidecarPort | null = null

if (typeof window !== 'undefined' && window.api?.sidecar?.onStatus) {
  window.api.sidecar.onStatus((status) => {
    if (status.kind === 'restarted' && status.secretToken && cached) {
      cached = { ...cached, secretToken: status.secretToken }
    }
  })
}

async function getSidecarConfig(): Promise<SidecarPort> {
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

async function callJson<T>(path: string, init: RequestInit = {}): Promise<T> {
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
