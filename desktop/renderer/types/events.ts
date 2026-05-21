/**
 * Hand-written TS mirror of the SSE envelope schema from
 * `src/market_analyser/api/events/__init__.py` (Plan 0007 phase 2).
 *
 * Why hand-written: the pydantic payload models are internal to the event
 * bus, not bound to any FastAPI response_model, so they don't surface in
 * `components.schemas` of the OpenAPI dump — the gen-types pipeline can't
 * emit them automatically. The parity guard is `events.test.ts`, which
 * dumps `model_json_schema()` from each pydantic model and asserts the
 * literal sets + field names + required flags here match.
 *
 * If you change a pydantic field, change this file too, then run
 * `pnpm --filter desktop test -- events.test.ts` to confirm the parity
 * test still passes.
 */

export type OverlayKind = 'ema' | 'sma' | 'rsi' | 'macd' | 'bbands'

export interface OverlaySpec {
  kind: OverlayKind
  period?: number | null
}

export type MarkerKind = 'bullish_marker' | 'bearish_marker'

export interface Marker {
  event_ts: string
  kind: MarkerKind
  label?: string | null
}

export interface ChartShowPayloadV1 {
  symbol: string
  timeframe: string
  range_start: string
  range_end: string
  overlays?: OverlaySpec[] | null
}

export interface ChartUpdatePayloadV1 {
  symbol: string
  timeframe: string
  overlays?: OverlaySpec[] | null
  range_start?: string | null
  range_end?: string | null
  focus_bar?: string | null
}

export interface ChartHighlightPayloadV1 {
  symbol: string
  timeframe: string
  markers: Marker[]
}

export interface RunCompletedPayloadV1 {
  kind: 'backtest' | 'analysis' | 'defi'
  run_id: string
  artifact_path: string
}

export type EnvelopeType =
  | 'chart.show'
  | 'chart.update'
  | 'chart.highlight'
  | 'run.completed'
  | 'chart.update_dropped'

export interface Envelope<T = unknown> {
  type: string
  version: number
  ts: string
  payload: T
}

export type ChartShowEnvelope = Envelope<ChartShowPayloadV1> & { type: 'chart.show'; version: 1 }
export type ChartUpdateEnvelope = Envelope<ChartUpdatePayloadV1> & {
  type: 'chart.update'
  version: 1
}
export type ChartHighlightEnvelope = Envelope<ChartHighlightPayloadV1> & {
  type: 'chart.highlight'
  version: 1
}
export type RunCompletedEnvelope = Envelope<RunCompletedPayloadV1> & {
  type: 'run.completed'
  version: 1
}
