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

export type OverlayKind = 'ema' | 'sma' | 'rsi' | 'macd' | 'bbands' | 'price_line' | 'supertrend'

/** Support/resistance role for a `price_line` overlay; absent for plain levels. */
export type PriceLineRole = 'support' | 'resistance'

/** Mirror of the pydantic `OverlaySpec`. One model carries two disjoint families
 * (the sidecar's `_validate_kind_fields` keeps them so): indicator overlays use
 * `period` (+ `supertrend`'s ATR `multiplier`); a `price_line` carries `price` +
 * `label` (+ optional `role`) — the channel the agent pushes S/R levels through
 * (Plan 0047). The non-applicable fields are absent on the wire (the bus dumps
 * with `exclude_none`). `supertrend` (Plan 0049) is an additive indicator kind. */
export interface OverlaySpec {
  kind: OverlayKind
  period?: number | null
  /** Supertrend's ATR multiplier (Plan 0049); absent on the other kinds. */
  multiplier?: number | null
  price?: number | null
  label?: string | null
  role?: PriceLineRole | null
}

export type MarkerKind = 'bullish_marker' | 'bearish_marker' | 'neutral_marker'

/** Mirror of the pydantic `Marker` (Plan 0049 / ADR-0045). The base shape is a
 * point arrow at `event_ts`; the additive fields carry first-class pattern
 * identity (`pattern`), an optional bar span for multi-bar patterns
 * (`span_start_ts`/`span_end_ts`, present together or not at all), and the
 * detector `strength`. `kind` gains `neutral_marker` so doji et al. render. All
 * additions are absent on the wire when unset (the bus dumps with
 * `exclude_none`), so a legacy point marker is `{event_ts, kind, label?}`. */
export interface Marker {
  event_ts: string
  kind: MarkerKind
  label?: string | null
  pattern?: string | null
  span_start_ts?: string | null
  span_end_ts?: string | null
  strength?: number | null
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

/** The most-recent signal in a live evaluation (Plan 0026). `kind` mirrors the
 * pydantic `SignalKind` StrEnum. `reason` is optional on the wire — the SSE bus
 * dumps with `exclude_none`, so it is absent (not `null`) when the strategy
 * emitted no reason. */
export interface EvaluatedSignal {
  kind: 'enter_long' | 'exit_long'
  bar_index: number
  /** ISO 8601 UTC timestamp. */
  event_ts: string
  reason?: string | null
}

/** A live strategy-vs-current-bar evaluation (Plan 0026). A condition report,
 * never a recommendation. `last_signal` / `bars_since_last_signal` are optional
 * on the wire (absent, via `exclude_none`, when no signal has fired — a flat
 * evaluation). `bars_since_last_signal === 0` means the signal fired on the last
 * closed bar (`fresh_signal === true`). */
export interface SignalEvaluation {
  strategy_id: string
  symbol: string
  timeframe: string
  /** ISO 8601 UTC timestamp of the last CLOSED bar fed to the strategy. */
  evaluated_through_ts: string
  closed_bar_count: number
  latest_bar_excluded_as_forming: boolean
  current_position: 'flat' | 'long'
  last_signal?: EvaluatedSignal | null
  bars_since_last_signal?: number | null
  fresh_signal: boolean
}

export interface SignalEvaluatedPayloadV1 {
  evaluation: SignalEvaluation
}

/** A single [start, end] coverage gap a backfill is/was filling (Plan 0013). */
export interface GapWindow {
  start: string
  end: string
}

export interface OhlcvBackfillStartedPayloadV1 {
  symbol: string
  timeframe: string
  gaps: GapWindow[]
}

export interface OhlcvBackfilledPayloadV1 {
  symbol: string
  timeframe: string
  range_start: string
  range_end: string
  bars_added: number
}

/** Closed set — mirror of the pydantic `Literal` on `OhlcvBackfillFailedPayloadV1.reason`. */
export type BackfillFailureReason = 'rate_limited' | 'upstream_unavailable' | 'unknown_symbol'

export interface OhlcvBackfillFailedPayloadV1 {
  symbol: string
  timeframe: string
  reason: BackfillFailureReason
  message: string
}

export type EnvelopeType =
  | 'chart.show'
  | 'chart.update'
  | 'chart.highlight'
  | 'run.completed'
  | 'signal.evaluated'
  | 'chart.update_dropped'
  | 'ohlcv.backfill_started'
  | 'ohlcv.backfilled'
  | 'ohlcv.backfill_failed'

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
export type SignalEvaluatedEnvelope = Envelope<SignalEvaluatedPayloadV1> & {
  type: 'signal.evaluated'
  version: 1
}
export type OhlcvBackfillStartedEnvelope = Envelope<OhlcvBackfillStartedPayloadV1> & {
  type: 'ohlcv.backfill_started'
  version: 1
}
export type OhlcvBackfilledEnvelope = Envelope<OhlcvBackfilledPayloadV1> & {
  type: 'ohlcv.backfilled'
  version: 1
}
export type OhlcvBackfillFailedEnvelope = Envelope<OhlcvBackfillFailedPayloadV1> & {
  type: 'ohlcv.backfill_failed'
  version: 1
}
