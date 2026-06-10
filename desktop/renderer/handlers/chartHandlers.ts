/**
 * Per-type chart-event handlers (Plan 0007 phase 4). Implemented as a
 * pure reducer so state transitions are atomic, serializable, and easy
 * to unit-test without React.
 *
 * `chart.show` resets the active chart context (symbol/timeframe/range/
 * overlays) and clears the in-memory live-highlights buffer.
 *
 * `chart.update` applies a delta IF the update's symbol+timeframe match
 * the current chart; otherwise — per ADR-0017's out-of-order rule — it
 * is treated as a `chart.show` with the available fields. The wire format
 * omits unset optional fields (`exclude_none=True`), so a missing key
 * means "leave unchanged"; we use `payload.X ?? prev.X` for every
 * optional field rather than a `null`-aware merge.
 *
 * `chart.highlight` only buffers markers for the active chart and
 * deduplicates by `(event_ts, kind)` against the existing buffer. The
 * polled `useAnnotationsPoll` (Plan 0006) keeps running unchanged; the
 * OhlcvView merges the live buffer with the polled list, deduping again
 * on the same key, so the polled DB row arriving ~1 s later replaces the
 * live marker without creating a visual duplicate.
 */
import { DEFAULT_TIMEFRAME, KNOWN_TIMEFRAMES, type Timeframe } from '../lib/timeframes'
import type {
  ChartHighlightPayloadV1,
  ChartShowPayloadV1,
  ChartUpdatePayloadV1,
  Marker,
  OverlaySpec,
  TrendlineSpec,
} from '../types/events'

export interface ChartState {
  symbol: string
  timeframe: Timeframe
  /** ISO 8601 UTC start of the visible range, inclusive. */
  range_start: string
  /** ISO 8601 UTC end of the visible range, inclusive. */
  range_end: string
  overlays: OverlaySpec[]
  /** Sloped trendlines from `chart.show`/`chart.update` (Plan 0052, ADR-0049).
   * Same merge rule as `overlays`: a missing key on an update means "leave
   * unchanged"; cleared on a symbol/timeframe switch (the geometry belongs to
   * the chart it was computed for). */
  trendlines: TrendlineSpec[]
  /** Live markers from `chart.highlight` envelopes. Deduplicated by
   * `(event_ts, pattern, kind)` (Plan 0049) so distinct same-bar patterns
   * survive. Merged with the polled annotation list at render time — duplicates
   * between the two sources are resolved on the same key. */
  liveHighlights: Marker[]
}

export type ChartAction =
  | { kind: 'event/chart.show'; payload: ChartShowPayloadV1 }
  | { kind: 'event/chart.update'; payload: ChartUpdatePayloadV1 }
  | { kind: 'event/chart.highlight'; payload: ChartHighlightPayloadV1 }
  | { kind: 'ui/set-symbol'; symbol: string }
  | { kind: 'ui/set-timeframe'; timeframe: Timeframe }
  | { kind: 'ui/refresh'; nowIso: string; lookbackDays: number }

// The MCP boundary rejects anything not in the canonical timeframe set
// (`lib/timeframes`, mirroring the backend), so a payload arriving here with an
// exotic value indicates a bug upstream. We narrow defensively to the default
// instead of crashing — but a genuinely-supported 15m/4h/1w now passes through
// unchanged (previously this coerced everything outside {1d,1h} to 1d).
function asTimeframe(value: string): Timeframe {
  return KNOWN_TIMEFRAMES.has(value) ? (value as Timeframe) : DEFAULT_TIMEFRAME
}

/** Dedup key (Plan 0049 / ADR-0045): `event_ts | pattern | kind`. Keying on
 * `pattern` lets two DISTINCT patterns on the same bar+direction (a doji and a
 * hammer) both survive; a true duplicate (same pattern) still collapses. A marker
 * without a `pattern` (the legacy `highlight_pattern` path) falls back to the old
 * `(event_ts, kind)` behaviour via the empty-string segment. */
function highlightKey(m: Marker): string {
  return `${m.event_ts}|${m.pattern ?? ''}|${m.kind}`
}

function dedupHighlights(existing: Marker[], incoming: Marker[]): Marker[] {
  if (incoming.length === 0) return existing
  const seen = new Set(existing.map(highlightKey))
  const additions = incoming.filter((m) => !seen.has(highlightKey(m)))
  return additions.length === 0 ? existing : [...existing, ...additions]
}

export function applyChartShow(_prev: ChartState, payload: ChartShowPayloadV1): ChartState {
  return {
    symbol: payload.symbol,
    timeframe: asTimeframe(payload.timeframe),
    range_start: payload.range_start,
    range_end: payload.range_end,
    overlays: payload.overlays ?? [],
    trendlines: payload.trendlines ?? [],
    liveHighlights: [],
  }
}

export function applyChartUpdate(prev: ChartState, payload: ChartUpdatePayloadV1): ChartState {
  const isContinuation = prev.symbol === payload.symbol && prev.timeframe === payload.timeframe

  if (!isContinuation) {
    // ADR-0017: out-of-order update OR update for a different chart →
    // treat as `chart.show` with whatever fields the payload carries.
    // Range fields fall back to `prev` because the chart needs *some*
    // window, and the prior window is the closest reasonable default.
    return {
      symbol: payload.symbol,
      timeframe: asTimeframe(payload.timeframe),
      range_start: payload.range_start ?? prev.range_start,
      range_end: payload.range_end ?? prev.range_end,
      overlays: payload.overlays ?? [],
      trendlines: payload.trendlines ?? [],
      liveHighlights: [],
    }
  }

  return {
    ...prev,
    overlays: payload.overlays ?? prev.overlays,
    trendlines: payload.trendlines ?? prev.trendlines,
    range_start: payload.range_start ?? prev.range_start,
    range_end: payload.range_end ?? prev.range_end,
  }
}

export function applyChartHighlight(
  prev: ChartState,
  payload: ChartHighlightPayloadV1,
): ChartState {
  if (prev.symbol !== payload.symbol || prev.timeframe !== payload.timeframe) {
    // Highlight is for a non-active chart — drop. The agent can issue a
    // `chart.show` first if it wants the markers visible.
    return prev
  }
  const merged = dedupHighlights(prev.liveHighlights, payload.markers)
  if (merged === prev.liveHighlights) return prev
  return { ...prev, liveHighlights: merged }
}

export function chartReducer(state: ChartState, action: ChartAction): ChartState {
  switch (action.kind) {
    case 'event/chart.show':
      return applyChartShow(state, action.payload)
    case 'event/chart.update':
      return applyChartUpdate(state, action.payload)
    case 'event/chart.highlight':
      return applyChartHighlight(state, action.payload)
    case 'ui/set-symbol':
      if (state.symbol === action.symbol) return state
      return { ...state, symbol: action.symbol, liveHighlights: [], trendlines: [] }
    case 'ui/set-timeframe':
      if (state.timeframe === action.timeframe) return state
      return { ...state, timeframe: action.timeframe, liveHighlights: [], trendlines: [] }
    case 'ui/refresh': {
      const endMs = Date.parse(action.nowIso)
      const startMs = endMs - action.lookbackDays * 24 * 60 * 60 * 1000
      return {
        ...state,
        range_start: new Date(startMs).toISOString(),
        range_end: action.nowIso,
      }
    }
  }
}

export const DEFAULT_LOOKBACK_DAYS = 365

export function initialChartState(nowIso: string = new Date().toISOString()): ChartState {
  const endMs = Date.parse(nowIso)
  const startMs = endMs - DEFAULT_LOOKBACK_DAYS * 24 * 60 * 60 * 1000
  return {
    symbol: 'AAPL',
    timeframe: DEFAULT_TIMEFRAME,
    range_start: new Date(startMs).toISOString(),
    range_end: nowIso,
    overlays: [],
    trendlines: [],
    liveHighlights: [],
  }
}
