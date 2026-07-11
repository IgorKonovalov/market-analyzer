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

export type OverlayKind =
  | 'ema'
  | 'sma'
  | 'rsi'
  | 'macd'
  | 'bbands'
  | 'price_line'
  | 'supertrend'
  | 'ichimoku'

/** Support/resistance role for a `price_line` overlay; absent for plain levels. */
export type PriceLineRole = 'support' | 'resistance'

/** Mirror of the pydantic `OverlaySpec`. One model carries two disjoint families
 * (the sidecar's `_validate_kind_fields` keeps them so): indicator overlays use
 * `period` (+ `supertrend`'s ATR `multiplier`); a `price_line` carries `price` +
 * `label` (+ optional `role`) — the channel the agent pushes S/R levels through
 * (Plan 0047). The non-applicable fields are absent on the wire (the bus dumps
 * with `exclude_none`). `supertrend` (Plan 0049) is an additive indicator kind;
 * `ichimoku` (Plan 0073) is another, carrying its own four optional period fields
 * (`conversion`/`base`/`span_b`/`displacement`) — absent ⇒ the renderer applies
 * the classic 9/26/52/26 defaults. */
export interface OverlaySpec {
  kind: OverlayKind
  period?: number | null
  /** Supertrend's ATR multiplier (Plan 0049); absent on the other kinds. */
  multiplier?: number | null
  /** Ichimoku period fields (Plan 0073); absent on the other kinds. */
  conversion?: number | null
  base?: number | null
  span_b?: number | null
  displacement?: number | null
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

/** Mirror of the pydantic `TrendPoint` (ADR-0049 / Plan 0052): one `(time, price)`
 * anchor of a trendline. `ts` is an ISO timestamp, not a bar index — consistent
 * with `Marker.event_ts`, so the renderer maps it the same way. */
export interface TrendPoint {
  ts: string
  price: number
}

export type TrendlineRole =
  | 'neckline'
  | 'upper_trendline'
  | 'lower_trendline'
  | 'projection'
  | 'skeleton'
  // The horizontal line through a double top/bottom's two matching extremes
  // (Plan 0083 ph8) — a plain 2-point line the renderer strokes with no special
  // handling; declared here so the wire role is type-complete.
  | 'base'

export type TrendlineStyle = 'solid' | 'dashed'

/** Mirror of the pydantic `TrendlineSpec` (ADR-0049 / Plan 0052): a sloped
 * multi-point line (a head-and-shoulders neckline, one bounding trendline of a
 * triangle/wedge). `points` carries ≥2 anchors (validated sidecar-side). `style`
 * is the forming-vs-confirmed cue (`dashed` = forming, `solid` = confirmed); it
 * has a pydantic default of `"solid"` (so it is not in the schema's `required`
 * set) but is never `None`, so under the bus's `exclude_none` dump it is ALWAYS
 * present on the wire — hence required here. `role`/`label`/`pattern` default to
 * `None` and are absent when unset. */
export interface TrendlineSpec {
  points: TrendPoint[]
  role?: TrendlineRole | null
  style: TrendlineStyle
  label?: string | null
  pattern?: string | null
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

/** Mirror of the pydantic `ChartTrendlinesPayloadV1` (ADR-0059 / Plan 0064):
 * sloped pattern lines on their OWN channel — moved off `chart.show`/`chart.update`
 * so a plain `chart.show` can't wipe them. Active-chart-gated in the reducer like
 * `chart.highlight`; recomputed from current bars (never persisted). */
export interface ChartTrendlinesPayloadV1 {
  symbol: string
  timeframe: string
  trendlines: TrendlineSpec[]
}

export interface RunCompletedPayloadV1 {
  kind: 'backtest' | 'analysis' | 'defi'
  run_id: string
  artifact_path: string
}

/** The most-recent signal in a live evaluation (Plan 0026). `kind` mirrors the
 * pydantic `SignalKind` StrEnum — flat/long/short since Plan 0053. `reason` is
 * optional on the wire — the SSE bus dumps with `exclude_none`, so it is absent
 * (not `null`) when the strategy emitted no reason. */
export interface EvaluatedSignal {
  kind: 'enter_long' | 'exit_long' | 'enter_short' | 'exit_short'
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
  current_position: 'flat' | 'long' | 'short'
  last_signal?: EvaluatedSignal | null
  bars_since_last_signal?: number | null
  fresh_signal: boolean
}

export interface SignalEvaluatedPayloadV1 {
  evaluation: SignalEvaluation
}

/** The JSON-representable value grain of a basis summary — mirror of the
 * pydantic `BasisValue` (flat scalars only, never nested dumps). */
export type BasisValue = number | string | boolean | null

/** Mirror of the pydantic `FusionCheck` (Plan 0063 / ADR-0058): one recorded
 * gate of the fusion trace — leg, check name, the real threshold-vs-actual
 * values, and the outcome. `threshold`/`actual` are required-but-nullable
 * `BasisValue`s (None marks a recorded fact with no pass bar, e.g. an
 * individual signal vote) and `exclude_none`-stripped from the wire — hence
 * optional here. `gating` (Plan 0077 phase 5 / ADR-0071) is whether the check
 * *blocks*: it has a non-None default (`True`) so it is not schema-required but
 * is never None, so `exclude_none` keeps it on the wire — hence required here
 * (the `checks` shape). The verdict is directional exactly when every *gating*
 * check passed (a demoted direction leg's checks ride `gating=false`). */
export interface FusionCheck {
  leg: 'forecast' | 'signal' | 'backtest' | 'conditions' | 'alignment'
  check: string
  threshold?: BasisValue
  actual?: BasisValue
  passed: boolean
  gating: boolean
}

/** Mirror of the pydantic `RecommendationBasis` (Plan 0038 / ADR-0029): what
 * backed the call. The original four fields are declared without defaults in
 * pydantic (so the JSON schema marks them required), but `backtest`/`forecast`
 * may be `None` — and the bus dumps with `exclude_none`, so on the wire those
 * keys are ABSENT (not null) when a flat recommendation lacks that leg. Hence
 * optional here. `checks` (Plan 0063) has a non-None default (`()`), so like
 * `ForecastProvenance.series_inputs` it is not schema-required but is ALWAYS
 * present on the wire — hence required here. `condition_codes`/`signal_codes`
 * (Plan 0069 phase 4b) are the translatable mirrors of `conditions`/`signals`:
 * one `ReasonCode` per prose line (1:1, same order), enum values as raw tokens
 * in `params`; defaulted `()` so not schema-required but always on the wire —
 * hence required here (the `checks` shape). */
export interface RecommendationBasis {
  conditions: string[]
  signals: string[]
  backtest?: Record<string, BasisValue> | null
  forecast?: Record<string, BasisValue> | null
  checks: FusionCheck[]
  condition_codes: ReasonCode[]
  signal_codes: ReasonCode[]
}

/** Mirror of the pydantic `ReasonCode` (Plan 0069 / ADR-0063): one structured,
 * translatable reason. `code` is a stable wire identifier the renderer keys off
 * (`t()`); `params` are raw values it interpolates (numbers stay numbers —
 * formatted `en-US`). Rides beside — never replaces — the English `rationale`/
 * `basis` prose. `params` has a `()`-style default (`{}`), so it is not
 * schema-required but is ALWAYS present on the wire — hence required here. */
export interface ReasonCode {
  code: string
  params: Record<string, number | string>
}

/** Mirror of the pydantic `DirectionLegStatus` (Plan 0077 phase 5 / ADR-0071):
 * the direction forecast leg's gating status on a verdict. Below the pinned
 * skill-margin threshold the leg is advisory, not gating — it cannot veto a
 * corroborated call nor decide one alone. `skill_margin` is the leg's
 * out-of-sample `skill - baseline_skill` (None when the forecast shipped no
 * scored edge, then `exclude_none`-stripped — hence optional here). Travels on
 * every verdict so the demotion is auditable beside the `basis.checks` gating
 * flags. */
export interface DirectionLegStatus {
  present: boolean
  gating: boolean
  skill_margin?: number | null
}

/** Mirror of the pydantic `VolatilitySizing` (Plan 0077 phase 5 / ADR-0071):
 * the non-voting volatility inputs to a directional call. Never directional —
 * a higher predicted vol only shrinks the size hint and widens the stop.
 * `size_factor` is a bounded relative inverse-vol multiplier (1.0 = the
 * reference vol) — an advisory number, never an order quantity. `vol_source`
 * says whether the trusted model prediction drove it, the deterministic
 * baseline reading did, or nothing was usable (`none` → neutral 1.0).
 * `vol_used`/`stop_vol_distance` are None (then `exclude_none`-stripped) when no
 * volatility drove the call — hence optional here. */
export interface VolatilitySizing {
  size_factor: number
  vol_used?: number | null
  vol_source: 'model' | 'baseline' | 'none'
  stop_vol_distance?: number | null
}

/** Mirror of the pydantic `RegimeContext` (Plan 0077 phase 5 / ADR-0071): the
 * non-voting regime context of a directional call. Feeds conviction only, never
 * direction: a trusted, unstable regime softens conviction, bounded and
 * direction-agnostic. `current_regime` is the trailing rule-based state (None,
 * then `exclude_none`-stripped, when undefined — hence optional); `trusted` is
 * whether the transition model beat its persistence baseline out-of-sample;
 * `conviction_factor` is the bounded (0, 1] multiplier it applied (1.0 =
 * neutral). */
export interface RegimeContext {
  current_regime?: string | null
  trusted: boolean
  conviction_factor: number
}

/** Mirror of the pydantic `Recommendation` (Plan 0038 / ADR-0029): the one
 * sanctioned advisory artifact. `label` can only ever be `"advisory"` —
 * pinned as a literal on both sides. `entry_zone`/`stop` are required-but-
 * nullable in pydantic; a flat recommendation dumps them as `None`, which
 * `exclude_none` strips from the wire — hence optional here (`targets` stays
 * required: an empty list survives the dump). `entry_zone` serialises as a
 * two-number `[low, high]` array. `reason_codes` (Plan 0069) has a non-None
 * default (`()`) — not schema-required but always on the wire — hence required
 * here (the `basis.checks` shape): one code per `rationale` line (1:1), then
 * one per `basis.checks` gate (1:1, same order). */
export interface Recommendation {
  symbol: string
  timeframe: string
  direction: 'long' | 'short' | 'flat'
  entry_zone?: [number, number] | null
  stop?: number | null
  targets: number[]
  conviction: number
  rationale: string[]
  basis: RecommendationBasis
  label: 'advisory'
  /** ISO 8601 UTC timestamp of the last bar the whole basis saw (anti-lookahead). */
  as_of_bar_ts: string
  reason_codes: ReasonCode[]
  /** Non-voting forecast inputs + the demoted direction leg (Plan 0077 phase 5 /
   * ADR-0071). `sizing`/`regime_context` shape a directional call and are None
   * on a flat verdict (then `exclude_none`-stripped); `direction_leg` travels on
   * every verdict. All defaulted None in pydantic → schema-optional, and
   * wire-absent when None — hence optional here. None can flip or manufacture a
   * direction. */
  sizing?: VolatilitySizing | null
  regime_context?: RegimeContext | null
  direction_leg?: DirectionLegStatus | null
}

export interface RecommendationCompletedPayloadV1 {
  recommendation: Recommendation
}

/** Mirror of the pydantic `RecommendationScoredPayloadV1` (Plan 0080 / ADR-0075):
 * the scheduled scorer resolved one matured advisory recommendation against
 * realized price. A FACT (how a past call turned out), never advice. Only scored
 * calls emit this, so `direction` is long/short (never flat) and `outcome_class`
 * is target_hit/stopped/timeout (never pending) — every measurement field is
 * populated. `forecast_prob` is required-but-nullable in pydantic (None for a
 * demoted no-edge forecast) and `exclude_none`-stripped from the wire — hence
 * optional here (the HorizonForecast `prob_*` shape). `directional_correct` is
 * the separate direction axis: a call can be directionally right yet score a
 * `stopped` loss (ADR-0075). Scalars only (ADR-0046). */
export interface RecommendationScoredPayloadV1 {
  symbol: string
  timeframe: string
  strategy_id: string
  direction: 'long' | 'short'
  /** ISO 8601 UTC timestamp of the as-of bar the call was made on. */
  as_of_bar_ts: string
  horizon_bars: number
  conviction: number
  forecast_prob?: number | null
  outcome_class: 'target_hit' | 'stopped' | 'timeout'
  realized_return: number
  realized_r: number
  directional_correct: boolean
  /** ISO 8601 UTC timestamp when the scorer resolved the call. */
  scored_at: string
}

/** Closed set — mirror of the pydantic `EdgeStrength` literal (Plan 0036/0059).
 * `no_edge` = the model did not beat baseline out-of-sample (prob_* are absent);
 * `marginal` / `clear` split a real beat by the sidecar's margin threshold so a
 * thin beat reads as thin. */
export type EdgeStrength = 'no_edge' | 'marginal' | 'clear'

/** Mirror of the pydantic `SeriesInput` (Plan 0059 / ADR-0054): one exogenous
 * metric series a forecast consumed. `last_point_ts` is required-but-nullable
 * in pydantic (epoch seconds of the freshest point the lag-1 join read); the
 * bus dumps with `exclude_none`, so it is ABSENT on the wire for an all-NaN
 * column — hence optional here. */
export interface SeriesInput {
  series_id: string
  last_point_ts?: number | null
}

/** Mirror of the pydantic `FoldSkill` (Plan 0036): one scored walk-forward
 * fold. The three `*_skill` fields are required-but-nullable (`None` marks an
 * unscored fold) and `exclude_none`-stripped from the wire — hence optional. */
export interface FoldSkill {
  fold_index: number
  n_test: number
  model_skill?: number | null
  persistence_skill?: number | null
  majority_skill?: number | null
}

/** Mirror of the pydantic `ForecastValidation` (Plan 0036 / ADR-0030): the
 * walk-forward verdict. `skill`/`baseline_skill`/`persistence_skill`/
 * `majority_skill` are required-but-nullable (None = nothing scorable) and
 * absent on the wire when None — hence optional here. `beats_baseline` is the
 * gate: false means no probability was shipped for that horizon. */
export interface ForecastValidation {
  horizon_bars: number
  n_splits: number
  n_scored: number
  skill?: number | null
  baseline_skill?: number | null
  persistence_skill?: number | null
  majority_skill?: number | null
  beats_baseline: boolean
  folds: FoldSkill[]
}

/** Mirror of the pydantic `ExplanationDriver` (Plan 0063 / ADR-0058): one
 * (feature, importance) pair of the compact explanation summary — the mean
 * out-of-sample permutation importance across the scored walk-forward folds.
 * Association within the validated model, not causation. */
export interface ExplanationDriver {
  feature: string
  importance: number
}

/** Mirror of the pydantic `ExplanationSummary` (Plan 0063 / ADR-0058): the
 * compact "why" riding beside the forecast — the top-N drivers (ordered,
 * importance descending; empty when the horizon had no scored folds) and the
 * runs_dir-relative path of the complete explanation JSON. `artifact` has a
 * None default (no runs_dir wired) and is `exclude_none`-stripped from the
 * wire — hence optional here; it is a provenance fact for display, never a
 * renderer filesystem target. `disclaimer_code`/`note_code` (Plan 0069) are the
 * translatable mirrors of the explanation's fixed disclaimer / no-scored-folds
 * prose: `disclaimer_code` is defaulted and always on the wire (required here);
 * `note_code` is set only for a horizon with no scored folds, else None and
 * `exclude_none`-stripped — hence optional here. */
export interface ExplanationSummary {
  top_drivers: ExplanationDriver[]
  artifact?: string | null
  disclaimer_code: string
  note_code?: string | null
}

/** Mirror of the pydantic `ForecastProvenance` (ADR-0040 / ADR-0054): the audit
 * trail that makes a forecast reproducible. `series_inputs` has a non-None
 * default (`()`), so like `TrendlineSpec.style` it is not schema-required but
 * is ALWAYS present on the wire (an empty array for a v1 model) — hence
 * required here. `fallback_reason` (Plan 0061) says why a v1 feature set was
 * used (store unwired, or wired but too starved for the requested
 * walk-forward); None when the v2 set genuinely ran, and then
 * `exclude_none`-stripped from the wire — hence optional here. `explanation`
 * (Plan 0063) is the compact ADR-0058 explanation summary, defaulted None and
 * wire-absent only when no explanation was computed — hence optional too. */
export interface ForecastProvenance {
  model_version: string
  feature_set_id: string
  /** ISO 8601 UTC timestamp. */
  training_cutoff: string
  seed: number
  lib_versions: Record<string, string>
  series_inputs: SeriesInput[]
  fallback_reason?: string | null
  explanation?: ExplanationSummary | null
}

/** Mirror of the pydantic `HorizonForecast` (Plan 0059 / ADR-0054): one
 * horizon's independently-validated block. `prob_*`/`edge_margin`/`provenance`
 * are required-but-nullable — None for a no-edge horizon (probs), an unscored
 * comparison (margin), or a horizon with nothing to train on (provenance) —
 * and `exclude_none`-stripped from the wire, hence optional here. */
export interface HorizonForecast {
  horizon_bars: number
  prob_up?: number | null
  prob_down?: number | null
  prob_flat?: number | null
  validation: ForecastValidation
  edge_margin?: number | null
  edge_strength: EdgeStrength
  provenance?: ForecastProvenance | null
}

/** Mirror of the pydantic `MultiHorizonForecastResult` (Plan 0059 / ADR-0054):
 * the `forecast` tool's response — one block per requested horizon, each
 * trained/validated/gated independently. A condition (a calibrated
 * probability), never a recommendation. */
export interface MultiHorizonForecastResult {
  symbol: string
  timeframe: string
  /** ISO 8601 UTC timestamp of the as-of bar (anti-lookahead). */
  as_of_bar_ts: string
  feature_set_id: string
  horizons: HorizonForecast[]
}

export interface ForecastCompletedPayloadV1 {
  forecast: MultiHorizonForecastResult
}

/** Closed set — mirror of the pydantic `BaselineKind` literal (Plan 0077): the
 * deterministic volatility baseline the model is scored against. */
export type BaselineKind = 'ewma' | 'persistence'

/** Mirror of the pydantic `VolatilityFoldScore` (Plan 0077 phase 1): one scored
 * walk-forward fold's out-of-sample QLIKE. The three `*_qlike` fields are
 * required-but-nullable (None marks an unscored fold) and `exclude_none`-stripped
 * from the wire — hence optional here. */
export interface VolatilityFoldScore {
  fold_index: number
  n_test: number
  model_qlike?: number | null
  persistence_qlike?: number | null
  ewma_qlike?: number | null
}

/** Mirror of the pydantic `VolatilityValidation` (Plan 0077 phase 1 / ADR-0070):
 * the regression verdict. `model_qlike` is pooled out-of-sample QLIKE (lower is
 * better); `baseline_qlike` the better of the two naive baselines; `beats_baseline`
 * the gate. The nullable scalars are None when nothing scored and
 * `exclude_none`-stripped from the wire — hence optional here. */
export interface VolatilityValidation {
  horizon_bars: number
  n_splits: number
  n_scored: number
  model_qlike?: number | null
  baseline_qlike?: number | null
  baseline_kind?: BaselineKind | null
  persistence_qlike?: number | null
  ewma_qlike?: number | null
  score_margin?: number | null
  beats_baseline: boolean
  folds: VolatilityFoldScore[]
}

/** Mirror of the pydantic `VolatilityForecast` (Plan 0077 phase 1 / ADR-0070): a
 * realised-volatility forecast for the next `horizon_bars`. A magnitude, never a
 * direction (ADR-0029). `predicted_vol`/`band` are None when no model trained (then
 * the baseline is the honest answer); `beats_baseline` says whether to trust the
 * model over `baseline_vol`. The nullable scalars are `exclude_none`-stripped from
 * the wire — hence optional here. `band` serialises as a two-number `[low, high]`. */
export interface VolatilityForecast {
  symbol: string
  timeframe: string
  /** ISO 8601 UTC timestamp of the as-of bar (anti-lookahead). */
  as_of_bar_ts: string
  horizon_bars: number
  predicted_vol?: number | null
  band?: [number, number] | null
  baseline_vol?: number | null
  baseline_kind?: BaselineKind | null
  beats_baseline: boolean
  score_margin?: number | null
  validation: VolatilityValidation
  provenance?: ForecastProvenance | null
}

export interface VolatilityForecastCompletedPayloadV1 {
  forecast: VolatilityForecast
}

/** Closed set — mirror of the pydantic `RegimeState` StrEnum (Plan 0077 phase 2):
 * the 6-value regime taxonomy, the product of the reused trend axis (up/down/
 * sideways) and a quiet/volatile volatility axis. */
export type RegimeState =
  | 'down_quiet'
  | 'down_volatile'
  | 'sideways_quiet'
  | 'sideways_volatile'
  | 'up_quiet'
  | 'up_volatile'

/** Mirror of the pydantic `RegimeFoldScore` (Plan 0077 phase 2): one scored
 * walk-forward fold's out-of-sample multiclass Brier score. Both `*_brier`
 * fields are required-but-nullable (None marks an unscored fold) and
 * `exclude_none`-stripped from the wire — hence optional here. */
export interface RegimeFoldScore {
  fold_index: number
  n_test: number
  model_brier?: number | null
  persistence_brier?: number | null
}

/** Mirror of the pydantic `RegimeValidation` (Plan 0077 phase 2 / ADR-0070): the
 * transition verdict. `model_brier` is pooled out-of-sample Brier (lower is
 * better); `persistence_brier` the "regime unchanged" baseline; `beats_baseline`
 * the gate. The nullable scalars are None when nothing scored and
 * `exclude_none`-stripped from the wire — hence optional here. */
export interface RegimeValidation {
  horizon_bars: number
  n_splits: number
  n_scored: number
  model_brier?: number | null
  persistence_brier?: number | null
  score_margin?: number | null
  beats_baseline: boolean
  folds: RegimeFoldScore[]
}

/** Mirror of the pydantic `RegimeForecast` (Plan 0077 phase 2 / ADR-0070): a
 * regime-transition forecast. `current_regime` is the trailing rule-based state
 * at the as-of bar; `transition_probs` the model's probability over next-period
 * regimes (None when no model trained — then persistence, i.e. the regime stays,
 * is the honest fallback). Both are None-able and `exclude_none`-stripped from the
 * wire — hence optional here. `transition_probs` serialises as an object keyed by
 * `RegimeState`. A condition report, never a recommendation (ADR-0029). */
export interface RegimeForecast {
  symbol: string
  timeframe: string
  /** ISO 8601 UTC timestamp of the as-of bar (anti-lookahead). */
  as_of_bar_ts: string
  horizon_bars: number
  current_regime?: RegimeState | null
  transition_probs?: Partial<Record<RegimeState, number>> | null
  beats_baseline: boolean
  score_margin?: number | null
  validation: RegimeValidation
  provenance?: ForecastProvenance | null
}

export interface RegimeForecastCompletedPayloadV1 {
  forecast: RegimeForecast
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

/** Closed set — mirror of the pydantic `Literal` on `AlertTriggeredPayloadV1.kind`. */
export type WatchKind = 'indicator_threshold' | 'pattern' | 'strategy_signal'

/** Mirror of the pydantic `AlertTriggeredPayloadV1` (Plan 0060 / ADR-0055): a
 * watch's condition transitioned false→true. Condition-only by construction
 * (ADR-0029) — `condition` is the human-readable fact (e.g. `rsi 28.44 < 30`),
 * `values` the numbers behind it; there is deliberately no direction/action/
 * conviction field. Unlike the older event mirrors, this payload is ALSO
 * Zod-validated at the SSE boundary (`schemas/alertTriggered.ts`) — the
 * standing SSE-validation follow-up pattern starts here. */
export interface AlertTriggeredPayloadV1 {
  watch_id: number
  symbol: string
  timeframe: string
  kind: WatchKind
  /** ISO 8601 UTC timestamp. */
  fired_at: string
  condition: string
  values: Record<string, number>
}

export type EnvelopeType =
  | 'chart.show'
  | 'chart.update'
  | 'chart.highlight'
  | 'chart.trendlines'
  | 'run.completed'
  | 'signal.evaluated'
  | 'recommendation.completed'
  | 'recommendation.scored'
  | 'forecast.completed'
  | 'volatility_forecast.completed'
  | 'regime_forecast.completed'
  | 'chart.update_dropped'
  | 'ohlcv.backfill_started'
  | 'ohlcv.backfilled'
  | 'ohlcv.backfill_failed'
  | 'alert.triggered'

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
export type ChartTrendlinesEnvelope = Envelope<ChartTrendlinesPayloadV1> & {
  type: 'chart.trendlines'
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
export type RecommendationCompletedEnvelope = Envelope<RecommendationCompletedPayloadV1> & {
  type: 'recommendation.completed'
  version: 1
}
export type RecommendationScoredEnvelope = Envelope<RecommendationScoredPayloadV1> & {
  type: 'recommendation.scored'
  version: 1
}
export type ForecastCompletedEnvelope = Envelope<ForecastCompletedPayloadV1> & {
  type: 'forecast.completed'
  version: 1
}
export type VolatilityForecastCompletedEnvelope = Envelope<VolatilityForecastCompletedPayloadV1> & {
  type: 'volatility_forecast.completed'
  version: 1
}
export type RegimeForecastCompletedEnvelope = Envelope<RegimeForecastCompletedPayloadV1> & {
  type: 'regime_forecast.completed'
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
export type AlertTriggeredEnvelope = Envelope<AlertTriggeredPayloadV1> & {
  type: 'alert.triggered'
  version: 1
}
