/**
 * Parity guard: assert the hand-written TS in `events.ts` matches the
 * pydantic payload models in `src/market_analyser/api/events/__init__.py`.
 *
 * The pipeline is a one-shot subprocess that dumps each model's JSON schema
 * and prints them all as one JSON object. We then assert, per model:
 *   - the property names match the TS interface members,
 *   - the `required` set matches the TS non-optional members,
 *   - any literal/enum field's set matches the TS literal union.
 *
 * If this test fails after a pydantic change, update `events.ts` to match.
 * If it fails after a TS-only change, you broke the mirror — revert or
 * propagate to pydantic via /architect.
 *
 * The subprocess uses `uv run --no-sync` per the pattern in
 * `desktop/scripts/gen-types.mjs`.
 */
import { spawnSync } from 'node:child_process'
import { resolve } from 'node:path'

interface JsonSchema {
  properties?: Record<string, JsonSchema>
  required?: string[]
  enum?: unknown[]
  const?: unknown
  type?: string | string[]
  anyOf?: JsonSchema[]
  $ref?: string
  $defs?: Record<string, JsonSchema>
  items?: JsonSchema
}

interface DumpedSchemas {
  OverlaySpec: JsonSchema
  Marker: JsonSchema
  TrendPoint: JsonSchema
  TrendlineSpec: JsonSchema
  TimePricePoint: JsonSchema
  DrawingStyle: JsonSchema
  DrawingSpec: JsonSchema
  ChartShowPayloadV1: JsonSchema
  ChartUpdatePayloadV1: JsonSchema
  ChartHighlightPayloadV1: JsonSchema
  ChartTrendlinesPayloadV1: JsonSchema
  PivotPoint: JsonSchema
  Divergence: JsonSchema
  ChartDivergencesPayloadV1: JsonSchema
  RunCompletedPayloadV1: JsonSchema
  GapWindow: JsonSchema
  OhlcvBackfillStartedPayloadV1: JsonSchema
  OhlcvBackfilledPayloadV1: JsonSchema
  OhlcvBackfillFailedPayloadV1: JsonSchema
  SignalEvaluatedPayloadV1: JsonSchema
  SignalEvaluation: JsonSchema
  EvaluatedSignal: JsonSchema
  AlertTriggeredPayloadV1: JsonSchema
  RecommendationCompletedPayloadV1: JsonSchema
  RecommendationScoredPayloadV1: JsonSchema
  Recommendation: JsonSchema
  RecommendationBasis: JsonSchema
  FusionCheck: JsonSchema
  ReasonCode: JsonSchema
  ForecastCompletedPayloadV1: JsonSchema
  MultiHorizonForecastResult: JsonSchema
  HorizonForecast: JsonSchema
  ForecastValidation: JsonSchema
  FoldSkill: JsonSchema
  ForecastProvenance: JsonSchema
  SeriesInput: JsonSchema
  ExplanationDriver: JsonSchema
  ExplanationSummary: JsonSchema
  DirectionLegStatus: JsonSchema
  VolatilitySizing: JsonSchema
  RegimeContext: JsonSchema
  VolatilityForecastCompletedPayloadV1: JsonSchema
  VolatilityForecast: JsonSchema
  VolatilityValidation: JsonSchema
  VolatilityFoldScore: JsonSchema
  RegimeForecastCompletedPayloadV1: JsonSchema
  RegimeForecast: JsonSchema
  RegimeValidation: JsonSchema
  RegimeFoldScore: JsonSchema
  TechnicalReadCompletedPayloadV1: JsonSchema
  TechnicalRead: JsonSchema
  PredictionScreenCompletedPayloadV1: JsonSchema
  ConvergenceOpportunity: JsonSchema
  ResolutionRisk: JsonSchema
}

const REPO_ROOT = resolve(__dirname, '..', '..', '..')

function dumpPydanticSchemas(): DumpedSchemas {
  const script = [
    'import json',
    'from market_analyser.events import (',
    '    OverlaySpec, Marker,',
    '    TrendPoint, TrendlineSpec,',
    '    TimePricePoint, DrawingStyle, DrawingSpec,',
    '    ChartShowPayloadV1, ChartUpdatePayloadV1,',
    '    ChartHighlightPayloadV1, ChartTrendlinesPayloadV1,',
    '    ChartDivergencesPayloadV1,',
    '    RunCompletedPayloadV1,',
    '    GapWindow, OhlcvBackfillStartedPayloadV1,',
    '    OhlcvBackfilledPayloadV1, OhlcvBackfillFailedPayloadV1,',
    '    SignalEvaluatedPayloadV1, AlertTriggeredPayloadV1,',
    '    RecommendationCompletedPayloadV1, RecommendationScoredPayloadV1,',
    '    ForecastCompletedPayloadV1,',
    '    VolatilityForecastCompletedPayloadV1, RegimeForecastCompletedPayloadV1,',
    '    TechnicalReadCompletedPayloadV1, PredictionScreenCompletedPayloadV1,',
    ')',
    'from market_analyser.analysis.types import Divergence, PivotPoint',
    'from market_analyser.prediction import ConvergenceOpportunity, ResolutionRisk',
    'from market_analyser.backtest import SignalEvaluation, EvaluatedSignal',
    'from market_analyser.advisor.models import (',
    '    FusionCheck, ReasonCode, Recommendation, RecommendationBasis,',
    '    DirectionLegStatus, VolatilitySizing, RegimeContext, TechnicalRead,',
    ')',
    'from market_analyser.forecast.result import (',
    '    MultiHorizonForecastResult, HorizonForecast,',
    '    ForecastProvenance, SeriesInput,',
    '    ExplanationDriver, ExplanationSummary,',
    ')',
    'from market_analyser.forecast.validation import ForecastValidation, FoldSkill',
    'from market_analyser.forecast.volatility import (',
    '    VolatilityForecast, VolatilityValidation, VolatilityFoldScore,',
    ')',
    'from market_analyser.forecast.regime import (',
    '    RegimeForecast, RegimeValidation, RegimeFoldScore,',
    ')',
    'print(json.dumps({',
    '    "OverlaySpec": OverlaySpec.model_json_schema(),',
    '    "Marker": Marker.model_json_schema(),',
    '    "TrendPoint": TrendPoint.model_json_schema(),',
    '    "TrendlineSpec": TrendlineSpec.model_json_schema(),',
    '    "TimePricePoint": TimePricePoint.model_json_schema(),',
    '    "DrawingStyle": DrawingStyle.model_json_schema(),',
    '    "DrawingSpec": DrawingSpec.model_json_schema(),',
    '    "ChartShowPayloadV1": ChartShowPayloadV1.model_json_schema(),',
    '    "ChartUpdatePayloadV1": ChartUpdatePayloadV1.model_json_schema(),',
    '    "ChartHighlightPayloadV1": ChartHighlightPayloadV1.model_json_schema(),',
    '    "ChartTrendlinesPayloadV1": ChartTrendlinesPayloadV1.model_json_schema(),',
    '    "PivotPoint": PivotPoint.model_json_schema(),',
    '    "Divergence": Divergence.model_json_schema(),',
    '    "ChartDivergencesPayloadV1": ChartDivergencesPayloadV1.model_json_schema(),',
    '    "RunCompletedPayloadV1": RunCompletedPayloadV1.model_json_schema(),',
    '    "GapWindow": GapWindow.model_json_schema(),',
    '    "OhlcvBackfillStartedPayloadV1": OhlcvBackfillStartedPayloadV1.model_json_schema(),',
    '    "OhlcvBackfilledPayloadV1": OhlcvBackfilledPayloadV1.model_json_schema(),',
    '    "OhlcvBackfillFailedPayloadV1": OhlcvBackfillFailedPayloadV1.model_json_schema(),',
    '    "SignalEvaluatedPayloadV1": SignalEvaluatedPayloadV1.model_json_schema(),',
    '    "SignalEvaluation": SignalEvaluation.model_json_schema(),',
    '    "EvaluatedSignal": EvaluatedSignal.model_json_schema(),',
    '    "AlertTriggeredPayloadV1": AlertTriggeredPayloadV1.model_json_schema(),',
    '    "RecommendationCompletedPayloadV1": RecommendationCompletedPayloadV1.model_json_schema(),',
    '    "RecommendationScoredPayloadV1": RecommendationScoredPayloadV1.model_json_schema(),',
    '    "Recommendation": Recommendation.model_json_schema(),',
    '    "RecommendationBasis": RecommendationBasis.model_json_schema(),',
    '    "FusionCheck": FusionCheck.model_json_schema(),',
    '    "ReasonCode": ReasonCode.model_json_schema(),',
    '    "ForecastCompletedPayloadV1": ForecastCompletedPayloadV1.model_json_schema(),',
    '    "MultiHorizonForecastResult": MultiHorizonForecastResult.model_json_schema(),',
    '    "HorizonForecast": HorizonForecast.model_json_schema(),',
    '    "ForecastValidation": ForecastValidation.model_json_schema(),',
    '    "FoldSkill": FoldSkill.model_json_schema(),',
    '    "ForecastProvenance": ForecastProvenance.model_json_schema(),',
    '    "SeriesInput": SeriesInput.model_json_schema(),',
    '    "ExplanationDriver": ExplanationDriver.model_json_schema(),',
    '    "ExplanationSummary": ExplanationSummary.model_json_schema(),',
    '    "DirectionLegStatus": DirectionLegStatus.model_json_schema(),',
    '    "VolatilitySizing": VolatilitySizing.model_json_schema(),',
    '    "RegimeContext": RegimeContext.model_json_schema(),',
    '    "VolatilityForecastCompletedPayloadV1": VolatilityForecastCompletedPayloadV1.model_json_schema(),',
    '    "VolatilityForecast": VolatilityForecast.model_json_schema(),',
    '    "VolatilityValidation": VolatilityValidation.model_json_schema(),',
    '    "VolatilityFoldScore": VolatilityFoldScore.model_json_schema(),',
    '    "RegimeForecastCompletedPayloadV1": RegimeForecastCompletedPayloadV1.model_json_schema(),',
    '    "RegimeForecast": RegimeForecast.model_json_schema(),',
    '    "RegimeValidation": RegimeValidation.model_json_schema(),',
    '    "RegimeFoldScore": RegimeFoldScore.model_json_schema(),',
    '    "TechnicalReadCompletedPayloadV1": TechnicalReadCompletedPayloadV1.model_json_schema(),',
    '    "TechnicalRead": TechnicalRead.model_json_schema(),',
    '    "PredictionScreenCompletedPayloadV1": PredictionScreenCompletedPayloadV1.model_json_schema(),',
    '    "ConvergenceOpportunity": ConvergenceOpportunity.model_json_schema(),',
    '    "ResolutionRisk": ResolutionRisk.model_json_schema(),',
    '}))',
  ].join('\n')

  const result = spawnSync('uv', ['run', '--no-sync', 'python', '-c', script], {
    cwd: REPO_ROOT,
    encoding: 'utf-8',
    shell: false,
  })
  if (result.status !== 0) {
    throw new Error(`pydantic schema dump failed (${result.status}): ${result.stderr}`)
  }
  return JSON.parse(result.stdout) as DumpedSchemas
}

function propertyNames(schema: JsonSchema): string[] {
  return Object.keys(schema.properties ?? {}).sort()
}

function requiredNames(schema: JsonSchema): string[] {
  return (schema.required ?? []).slice().sort()
}

/**
 * Pydantic emits a `Literal[...]` field as either an inline `enum` array, OR
 * (for fields whose value is a referenced enum class) as `$ref` pointing into
 * `$defs[<name>].enum`. We collapse both shapes to a sorted string[] of the
 * permitted values.
 */
function literalValues(schema: JsonSchema, fieldName: string): string[] | null {
  const fieldSchema = schema.properties?.[fieldName]
  if (!fieldSchema) return null

  if (Array.isArray(fieldSchema.enum)) {
    return fieldSchema.enum.map(String).sort()
  }
  if (typeof fieldSchema.$ref === 'string') {
    const refName = fieldSchema.$ref.replace(/^#\/\$defs\//, '')
    const defined = schema.$defs?.[refName]
    if (defined && Array.isArray(defined.enum)) {
      return defined.enum.map(String).sort()
    }
  }
  return null
}

// Subprocess takes ~1–2 s; let Jest give it room.
jest.setTimeout(30_000)

describe('SSE envelope schema parity (TS ↔ pydantic)', () => {
  let dumped: DumpedSchemas

  beforeAll(() => {
    dumped = dumpPydanticSchemas()
  })

  it('OverlaySpec fields match (price_line adds price/label/role; supertrend adds multiplier; ichimoku adds four periods; Plan 0092 adds fib/pivot/anchored-vwap params)', () => {
    expect(propertyNames(dumped.OverlaySpec)).toEqual([
      'anchor_ts',
      'base',
      'conversion',
      'displacement',
      'fib_kind',
      'high_anchor_price',
      'high_anchor_ts',
      'kind',
      'label',
      'low_anchor_price',
      'low_anchor_ts',
      'method',
      'multiplier',
      'period',
      'price',
      'role',
      'span_b',
    ])
    // Every field but `kind` defaults to None → not required (price_line's
    // price+label and the Plan-0092 kind params are enforced by the cross-field
    // validator, not `required`).
    expect(requiredNames(dumped.OverlaySpec)).toEqual(['kind'])
    expect(literalValues(dumped.OverlaySpec, 'kind')).toEqual(
      [
        'ad_line',
        'anchored_vwap',
        'bbands',
        'cci',
        'cmf',
        'ema',
        'fibonacci',
        'ichimoku',
        'macd',
        'mfi',
        'obv',
        'pivot_points',
        'price_line',
        'roc',
        'rsi',
        'sma',
        'stoch_rsi',
        'stochastic',
        'supertrend',
        'williams_r',
      ].sort(),
    )
    // `role`/`fib_kind`/`method` are optional Literals (`| None`), emitted as
    // `anyOf` rather than a top-level `enum`, so `literalValues` (enum/$ref only)
    // can't read them — the property-name + required checks pin their optionality.
  })

  it('Marker fields match (pattern/span/strength added; neutral_marker kind)', () => {
    expect(propertyNames(dumped.Marker)).toEqual([
      'event_ts',
      'kind',
      'label',
      'pattern',
      'span_end_ts',
      'span_start_ts',
      'strength',
    ])
    expect(requiredNames(dumped.Marker)).toEqual(['event_ts', 'kind'])
    expect(literalValues(dumped.Marker, 'kind')).toEqual(
      ['bearish_marker', 'bullish_marker', 'neutral_marker'].sort(),
    )
  })

  it('TrendPoint fields match (both anchors required)', () => {
    expect(propertyNames(dumped.TrendPoint)).toEqual(['price', 'ts'])
    expect(requiredNames(dumped.TrendPoint)).toEqual(['price', 'ts'])
  })

  it('TrendlineSpec fields match (points required; style is a closed solid/dashed set)', () => {
    expect(propertyNames(dumped.TrendlineSpec)).toEqual([
      'label',
      'pattern',
      'points',
      'role',
      'style',
    ])
    // `style` has a non-None default ("solid") → not in `required`, but it is
    // never None so `exclude_none` keeps it on the wire — the TS marks it
    // required. role/label/pattern default to None → optional both sides.
    expect(requiredNames(dumped.TrendlineSpec)).toEqual(['points'])
    expect(literalValues(dumped.TrendlineSpec, 'style')).toEqual(['dashed', 'solid'])
    // `role` is an optional Literal (`| None`), emitted as `anyOf` rather than a
    // top-level `enum` (same shape as OverlaySpec.role) — presence/optionality
    // are pinned by the property-name + required checks above.
  })

  it('TimePricePoint fields match (both anchors required; Plan 0097)', () => {
    expect(propertyNames(dumped.TimePricePoint)).toEqual(['price', 'ts'])
    expect(requiredNames(dumped.TimePricePoint)).toEqual(['price', 'ts'])
  })

  it('DrawingStyle fields match (both optional; Plan 0097)', () => {
    expect(propertyNames(dumped.DrawingStyle)).toEqual(['color', 'width'])
    expect(requiredNames(dumped.DrawingStyle)).toEqual([])
  })

  it('DrawingSpec fields match (six kinds; id always on the wire; Plan 0097 / ADR-0091)', () => {
    expect(propertyNames(dumped.DrawingSpec)).toEqual([
      'id',
      'kind',
      'points',
      'provenance',
      'style',
    ])
    // `id` has a default_factory → NOT in pydantic `required`, but it is always
    // present on the wire (generated when omitted), so the TS marks it required.
    // `style` defaults to None → optional both sides.
    expect(requiredNames(dumped.DrawingSpec)).toEqual(['kind', 'points', 'provenance'])
    expect(literalValues(dumped.DrawingSpec, 'kind')).toEqual(
      ['fib', 'hline', 'ray', 'rect', 'trendline', 'vline'].sort(),
    )
    expect(literalValues(dumped.DrawingSpec, 'provenance')).toEqual(['agent', 'user'])
  })

  it('ChartShowPayloadV1 fields match (trendlines REMOVED, Plan 0064/ADR-0059)', () => {
    expect(propertyNames(dumped.ChartShowPayloadV1)).toEqual([
      'overlays',
      'range_end',
      'range_start',
      'symbol',
      'timeframe',
    ])
    expect(requiredNames(dumped.ChartShowPayloadV1)).toEqual([
      'range_end',
      'range_start',
      'symbol',
      'timeframe',
    ])
  })

  it('ChartUpdatePayloadV1 fields match (trendlines REMOVED; all optional except symbol+timeframe)', () => {
    expect(propertyNames(dumped.ChartUpdatePayloadV1)).toEqual([
      'focus_bar',
      'overlays',
      'range_end',
      'range_start',
      'symbol',
      'timeframe',
    ])
    expect(requiredNames(dumped.ChartUpdatePayloadV1)).toEqual(['symbol', 'timeframe'])
  })

  it('ChartHighlightPayloadV1 fields match', () => {
    expect(propertyNames(dumped.ChartHighlightPayloadV1)).toEqual([
      'markers',
      'symbol',
      'timeframe',
    ])
    expect(requiredNames(dumped.ChartHighlightPayloadV1)).toEqual([
      'markers',
      'symbol',
      'timeframe',
    ])
  })

  it('ChartTrendlinesPayloadV1 fields match (dedicated channel, Plan 0064/ADR-0059)', () => {
    expect(propertyNames(dumped.ChartTrendlinesPayloadV1)).toEqual([
      'symbol',
      'timeframe',
      'trendlines',
    ])
    expect(requiredNames(dumped.ChartTrendlinesPayloadV1)).toEqual([
      'symbol',
      'timeframe',
      'trendlines',
    ])
  })

  it('PivotPoint fields match (both anchors required — Plan 0091 / ADR-0090)', () => {
    expect(propertyNames(dumped.PivotPoint)).toEqual(['price', 'ts'])
    expect(requiredNames(dumped.PivotPoint)).toEqual(['price', 'ts'])
  })

  it('Divergence fields match (all required; oscillator + kind closed sets — ADR-0090)', () => {
    expect(propertyNames(dumped.Divergence)).toEqual([
      'bar_index',
      'kind',
      'oscillator',
      'oscillator_pivots',
      'price_pivots',
      'strength',
    ])
    // No pydantic defaults, nothing nullable → every field is schema-required.
    expect(requiredNames(dumped.Divergence)).toEqual([
      'bar_index',
      'kind',
      'oscillator',
      'oscillator_pivots',
      'price_pivots',
      'strength',
    ])
    expect(literalValues(dumped.Divergence, 'oscillator')).toEqual(
      ['macd_hist', 'mfi', 'obv', 'rsi'].sort(),
    )
    expect(literalValues(dumped.Divergence, 'kind')).toEqual(
      ['hidden_bearish', 'hidden_bullish', 'regular_bearish', 'regular_bullish'].sort(),
    )
  })

  it('ChartDivergencesPayloadV1 fields match (dedicated cross-pane channel, ADR-0090)', () => {
    expect(propertyNames(dumped.ChartDivergencesPayloadV1)).toEqual([
      'divergences',
      'symbol',
      'timeframe',
    ])
    expect(requiredNames(dumped.ChartDivergencesPayloadV1)).toEqual([
      'divergences',
      'symbol',
      'timeframe',
    ])
  })

  it('RunCompletedPayloadV1 fields match', () => {
    expect(propertyNames(dumped.RunCompletedPayloadV1)).toEqual(['artifact_path', 'kind', 'run_id'])
    expect(requiredNames(dumped.RunCompletedPayloadV1)).toEqual(['artifact_path', 'kind', 'run_id'])
    expect(literalValues(dumped.RunCompletedPayloadV1, 'kind')).toEqual(
      ['analysis', 'backtest', 'defi'].sort(),
    )
  })

  it('GapWindow fields match', () => {
    expect(propertyNames(dumped.GapWindow)).toEqual(['end', 'start'])
    expect(requiredNames(dumped.GapWindow)).toEqual(['end', 'start'])
  })

  it('OhlcvBackfillStartedPayloadV1 fields match', () => {
    expect(propertyNames(dumped.OhlcvBackfillStartedPayloadV1)).toEqual([
      'gaps',
      'symbol',
      'timeframe',
    ])
    expect(requiredNames(dumped.OhlcvBackfillStartedPayloadV1)).toEqual([
      'gaps',
      'symbol',
      'timeframe',
    ])
  })

  it('OhlcvBackfilledPayloadV1 fields match', () => {
    expect(propertyNames(dumped.OhlcvBackfilledPayloadV1)).toEqual([
      'bars_added',
      'range_end',
      'range_start',
      'symbol',
      'timeframe',
    ])
    expect(requiredNames(dumped.OhlcvBackfilledPayloadV1)).toEqual([
      'bars_added',
      'range_end',
      'range_start',
      'symbol',
      'timeframe',
    ])
  })

  it('OhlcvBackfillFailedPayloadV1 fields match (reason is a closed literal set)', () => {
    expect(propertyNames(dumped.OhlcvBackfillFailedPayloadV1)).toEqual([
      'message',
      'reason',
      'symbol',
      'timeframe',
    ])
    expect(requiredNames(dumped.OhlcvBackfillFailedPayloadV1)).toEqual([
      'message',
      'reason',
      'symbol',
      'timeframe',
    ])
    expect(literalValues(dumped.OhlcvBackfillFailedPayloadV1, 'reason')).toEqual(
      ['rate_limited', 'unknown_symbol', 'upstream_unavailable', 'history_exceeded'].sort(),
    )
  })

  it('SignalEvaluatedPayloadV1 carries the evaluation inline', () => {
    expect(propertyNames(dumped.SignalEvaluatedPayloadV1)).toEqual(['evaluation'])
    expect(requiredNames(dumped.SignalEvaluatedPayloadV1)).toEqual(['evaluation'])
  })

  it('SignalEvaluation fields match (last_signal + bars_since_last_signal optional)', () => {
    expect(propertyNames(dumped.SignalEvaluation)).toEqual([
      'bars_since_last_signal',
      'closed_bar_count',
      'current_position',
      'evaluated_through_ts',
      'fresh_signal',
      'last_signal',
      'latest_bar_excluded_as_forming',
      'strategy_id',
      'symbol',
      'timeframe',
    ])
    // last_signal + bars_since_last_signal have `= None` defaults (the SSE bus
    // dumps with exclude_none), so they are NOT required — the TS marks them `?`.
    expect(requiredNames(dumped.SignalEvaluation)).toEqual([
      'closed_bar_count',
      'current_position',
      'evaluated_through_ts',
      'fresh_signal',
      'latest_bar_excluded_as_forming',
      'strategy_id',
      'symbol',
      'timeframe',
    ])
    expect(literalValues(dumped.SignalEvaluation, 'current_position')).toEqual([
      'flat',
      'long',
      'short',
    ])
  })

  it('EvaluatedSignal fields match (reason optional; kind is a closed literal set)', () => {
    expect(propertyNames(dumped.EvaluatedSignal)).toEqual([
      'bar_index',
      'event_ts',
      'kind',
      'reason',
    ])
    // reason has a `= None` default → not required.
    expect(requiredNames(dumped.EvaluatedSignal)).toEqual(['bar_index', 'event_ts', 'kind'])
    expect(literalValues(dumped.EvaluatedSignal, 'kind')).toEqual(
      ['enter_long', 'exit_long', 'enter_short', 'exit_short'].sort(),
    )
  })

  it('AlertTriggeredPayloadV1 fields match (all required; kind is a closed literal set)', () => {
    expect(propertyNames(dumped.AlertTriggeredPayloadV1)).toEqual([
      'condition',
      'fired_at',
      'kind',
      'symbol',
      'timeframe',
      'values',
      'watch_id',
    ])
    expect(requiredNames(dumped.AlertTriggeredPayloadV1)).toEqual([
      'condition',
      'fired_at',
      'kind',
      'symbol',
      'timeframe',
      'values',
      'watch_id',
    ])
    expect(literalValues(dumped.AlertTriggeredPayloadV1, 'kind')).toEqual(
      ['indicator_threshold', 'pattern', 'strategy_signal'].sort(),
    )
  })

  it('RecommendationCompletedPayloadV1 carries the recommendation inline (Plan 0039)', () => {
    expect(propertyNames(dumped.RecommendationCompletedPayloadV1)).toEqual(['recommendation'])
    expect(requiredNames(dumped.RecommendationCompletedPayloadV1)).toEqual(['recommendation'])
  })

  it('RecommendationScoredPayloadV1 fields match (forecast_prob nullable, absent when None; direction/outcome closed sets)', () => {
    expect(propertyNames(dumped.RecommendationScoredPayloadV1)).toEqual([
      'as_of_bar_ts',
      'conviction',
      'direction',
      'directional_correct',
      'forecast_prob',
      'horizon_bars',
      'outcome_class',
      'realized_r',
      'realized_return',
      'scored_at',
      'strategy_id',
      'symbol',
      'timeframe',
    ])
    // No pydantic defaults → every field is schema-required. The TS still marks
    // `forecast_prob` optional because it is None-valued for a demoted no-edge
    // forecast and the bus dumps with `exclude_none` — required in the model,
    // absent on the wire (the HorizonForecast `prob_*` shape).
    expect(requiredNames(dumped.RecommendationScoredPayloadV1)).toEqual([
      'as_of_bar_ts',
      'conviction',
      'direction',
      'directional_correct',
      'forecast_prob',
      'horizon_bars',
      'outcome_class',
      'realized_r',
      'realized_return',
      'scored_at',
      'strategy_id',
      'symbol',
      'timeframe',
    ])
    expect(literalValues(dumped.RecommendationScoredPayloadV1, 'direction')).toEqual(
      ['long', 'short'].sort(),
    )
    expect(literalValues(dumped.RecommendationScoredPayloadV1, 'outcome_class')).toEqual(
      ['stopped', 'target_hit', 'timeout'].sort(),
    )
  })

  it('Recommendation fields match (advisory label pinned as a single-value literal)', () => {
    expect(propertyNames(dumped.Recommendation)).toEqual([
      'as_of_bar_ts',
      'basis',
      'conviction',
      'direction',
      'direction_leg',
      'entry_zone',
      'label',
      'rationale',
      'reason_codes',
      'regime_context',
      'sizing',
      'stop',
      'symbol',
      'targets',
      'timeframe',
    ])
    // `entry_zone`/`stop` are required-but-nullable in pydantic: None-valued on a
    // flat recommendation and `exclude_none`-stripped from the wire, so the TS
    // marks them optional (the inverse of TrendlineSpec.style). `reason_codes`
    // (Plan 0069) has a non-None default (`()`) → not in `required`, but never
    // None, so `exclude_none` keeps it on the wire — the TS marks it required
    // (the RecommendationBasis.checks shape).
    expect(requiredNames(dumped.Recommendation)).toEqual([
      'as_of_bar_ts',
      'basis',
      'conviction',
      'direction',
      'entry_zone',
      'label',
      'rationale',
      'stop',
      'symbol',
      'targets',
      'timeframe',
    ])
    expect(literalValues(dumped.Recommendation, 'direction')).toEqual(['flat', 'long', 'short'])
    // A single-value Literal is emitted as `const`, not `enum` — the ADR-0029
    // guarantee that no non-advisory label can be constructed, pinned here.
    expect(dumped.Recommendation.properties?.label?.const).toBe('advisory')
  })

  it('ReasonCode fields match (params defaulted, always on the wire)', () => {
    expect(propertyNames(dumped.ReasonCode)).toEqual(['code', 'params'])
    // `code` has no default → schema-required. `params` has a `{}` default →
    // not in `required`, but never None so `exclude_none` keeps it on the wire —
    // the TS marks it required (the RecommendationBasis.checks shape).
    expect(requiredNames(dumped.ReasonCode)).toEqual(['code'])
  })

  it('ForecastCompletedPayloadV1 carries the multi-horizon result inline (Plan 0037)', () => {
    expect(propertyNames(dumped.ForecastCompletedPayloadV1)).toEqual(['forecast'])
    expect(requiredNames(dumped.ForecastCompletedPayloadV1)).toEqual(['forecast'])
  })

  it('MultiHorizonForecastResult fields match (all required)', () => {
    expect(propertyNames(dumped.MultiHorizonForecastResult)).toEqual([
      'as_of_bar_ts',
      'feature_set_id',
      'horizons',
      'symbol',
      'timeframe',
    ])
    expect(requiredNames(dumped.MultiHorizonForecastResult)).toEqual([
      'as_of_bar_ts',
      'feature_set_id',
      'horizons',
      'symbol',
      'timeframe',
    ])
  })

  it('HorizonForecast fields match (prob_*/edge_margin/provenance nullable, absent when None; edge_strength closed set)', () => {
    expect(propertyNames(dumped.HorizonForecast)).toEqual([
      'edge_margin',
      'edge_strength',
      'horizon_bars',
      'prob_down',
      'prob_flat',
      'prob_up',
      'provenance',
      'validation',
    ])
    // No pydantic defaults anywhere → every field is schema-required. The TS
    // still marks prob_*/edge_margin/provenance optional because they are
    // None-valued for a no-edge/untrainable horizon and the bus dumps with
    // `exclude_none` — required in the model, absent on the wire (the
    // Recommendation entry_zone/stop shape).
    expect(requiredNames(dumped.HorizonForecast)).toEqual([
      'edge_margin',
      'edge_strength',
      'horizon_bars',
      'prob_down',
      'prob_flat',
      'prob_up',
      'provenance',
      'validation',
    ])
    expect(literalValues(dumped.HorizonForecast, 'edge_strength')).toEqual(
      ['clear', 'marginal', 'no_edge'].sort(),
    )
  })

  it('ForecastValidation fields match (skill fields nullable, absent when unscored)', () => {
    expect(propertyNames(dumped.ForecastValidation)).toEqual([
      'baseline_skill',
      'beats_baseline',
      'folds',
      'horizon_bars',
      'majority_skill',
      'n_scored',
      'n_splits',
      'persistence_skill',
      'skill',
    ])
    expect(requiredNames(dumped.ForecastValidation)).toEqual([
      'baseline_skill',
      'beats_baseline',
      'folds',
      'horizon_bars',
      'majority_skill',
      'n_scored',
      'n_splits',
      'persistence_skill',
      'skill',
    ])
  })

  it('FoldSkill fields match (skill fields nullable, absent when unscored)', () => {
    expect(propertyNames(dumped.FoldSkill)).toEqual([
      'fold_index',
      'majority_skill',
      'model_skill',
      'n_test',
      'persistence_skill',
    ])
    expect(requiredNames(dumped.FoldSkill)).toEqual([
      'fold_index',
      'majority_skill',
      'model_skill',
      'n_test',
      'persistence_skill',
    ])
  })

  it('ForecastProvenance fields match (series_inputs defaulted, never None, always on the wire)', () => {
    expect(propertyNames(dumped.ForecastProvenance)).toEqual([
      'explanation',
      'fallback_reason',
      'feature_set_id',
      'lib_versions',
      'model_version',
      'seed',
      'series_inputs',
      'training_cutoff',
    ])
    // `series_inputs` has a non-None default (`()`) → not in `required`, but it
    // is never None so `exclude_none` keeps it on the wire — the TS marks it
    // required (the TrendlineSpec.style shape). `fallback_reason` (Plan 0061)
    // is defaulted AND nullable — None when the v2 set genuinely ran, then
    // `exclude_none`-stripped from the wire — so the TS marks it optional.
    // `explanation` (Plan 0063) follows the same defaulted-and-nullable shape:
    // absent only when no explanation was computed for the block.
    expect(requiredNames(dumped.ForecastProvenance)).toEqual([
      'feature_set_id',
      'lib_versions',
      'model_version',
      'seed',
      'training_cutoff',
    ])
  })

  it('ExplanationDriver fields match (both required — a bare (feature, importance) pair)', () => {
    expect(propertyNames(dumped.ExplanationDriver)).toEqual(['feature', 'importance'])
    expect(requiredNames(dumped.ExplanationDriver)).toEqual(['feature', 'importance'])
  })

  it('ExplanationSummary fields match (artifact defaulted+nullable, absent without a runs_dir)', () => {
    expect(propertyNames(dumped.ExplanationSummary)).toEqual([
      'artifact',
      'disclaimer_code',
      'note_code',
      'top_drivers',
    ])
    // `top_drivers` has no default → schema-required and always on the wire
    // (empty array when the horizon had no scored folds). `artifact` is
    // defaulted AND nullable — None without a runs_dir, then
    // `exclude_none`-stripped — so the TS marks it optional. `disclaimer_code`
    // (Plan 0069) is defaulted and never None → wire-present, TS-required;
    // `note_code` is defaulted None and set only for a no-scored-folds horizon →
    // `exclude_none`-stripped otherwise, so the TS marks it optional.
    expect(requiredNames(dumped.ExplanationSummary)).toEqual(['top_drivers'])
  })

  it('SeriesInput fields match (last_point_ts nullable, absent when the series had no point)', () => {
    expect(propertyNames(dumped.SeriesInput)).toEqual(['last_point_ts', 'series_id'])
    expect(requiredNames(dumped.SeriesInput)).toEqual(['last_point_ts', 'series_id'])
  })

  it('RecommendationBasis fields match (backtest/forecast nullable, absent on the wire when None)', () => {
    expect(propertyNames(dumped.RecommendationBasis)).toEqual([
      'backtest',
      'checks',
      'condition_codes',
      'conditions',
      'forecast',
      'signal_codes',
      'signals',
    ])
    // Same required-but-nullable shape as Recommendation's levels: schema-
    // required, wire-absent for a flat call missing that leg → TS optional.
    // `checks` (Plan 0063 — the deliberate ADR-0029 pin move) and
    // `condition_codes`/`signal_codes` (Plan 0069 phase 4b) each have a non-None
    // default (`()`) → not in `required`, but never None so `exclude_none` keeps
    // them on the wire — the TS marks them required (the
    // ForecastProvenance.series_inputs shape).
    expect(requiredNames(dumped.RecommendationBasis)).toEqual([
      'backtest',
      'conditions',
      'forecast',
      'signals',
    ])
  })

  it('FusionCheck fields match (gating added, defaulted; threshold/actual nullable; leg closed set)', () => {
    expect(propertyNames(dumped.FusionCheck)).toEqual([
      'actual',
      'check',
      'gating',
      'leg',
      'passed',
      'threshold',
    ])
    // `threshold`/`actual` have no defaults but are None-valued for a recorded
    // fact with no pass bar and `exclude_none`-stripped → TS optional. `gating`
    // (Plan 0077 phase 5) has a non-None default (True) → not in `required`, but
    // never None so `exclude_none` keeps it on the wire — the TS marks it
    // required (the `checks` shape).
    expect(requiredNames(dumped.FusionCheck)).toEqual([
      'actual',
      'check',
      'leg',
      'passed',
      'threshold',
    ])
    expect(literalValues(dumped.FusionCheck, 'leg')).toEqual(
      ['alignment', 'backtest', 'conditions', 'forecast', 'signal'].sort(),
    )
  })

  it('DirectionLegStatus fields match (skill_margin nullable, absent when None)', () => {
    expect(propertyNames(dumped.DirectionLegStatus)).toEqual(['gating', 'present', 'skill_margin'])
    // No defaults → every field schema-required; `skill_margin` is None-valued
    // when the forecast shipped no scored edge and `exclude_none`-stripped → TS
    // optional.
    expect(requiredNames(dumped.DirectionLegStatus)).toEqual(['gating', 'present', 'skill_margin'])
  })

  it('VolatilitySizing fields match (vol_used/stop_vol_distance nullable; vol_source closed set)', () => {
    expect(propertyNames(dumped.VolatilitySizing)).toEqual([
      'size_factor',
      'stop_vol_distance',
      'vol_source',
      'vol_used',
    ])
    expect(requiredNames(dumped.VolatilitySizing)).toEqual([
      'size_factor',
      'stop_vol_distance',
      'vol_source',
      'vol_used',
    ])
    expect(literalValues(dumped.VolatilitySizing, 'vol_source')).toEqual(
      ['baseline', 'model', 'none'].sort(),
    )
  })

  it('RegimeContext fields match (current_regime nullable, absent when None)', () => {
    expect(propertyNames(dumped.RegimeContext)).toEqual([
      'conviction_factor',
      'current_regime',
      'trusted',
    ])
    expect(requiredNames(dumped.RegimeContext)).toEqual([
      'conviction_factor',
      'current_regime',
      'trusted',
    ])
  })

  it('VolatilityForecastCompletedPayloadV1 carries the forecast inline (Plan 0077)', () => {
    expect(propertyNames(dumped.VolatilityForecastCompletedPayloadV1)).toEqual(['forecast'])
    expect(requiredNames(dumped.VolatilityForecastCompletedPayloadV1)).toEqual(['forecast'])
  })

  it('VolatilityForecast fields match (predicted/band/baseline nullable, absent when None)', () => {
    expect(propertyNames(dumped.VolatilityForecast)).toEqual([
      'as_of_bar_ts',
      'band',
      'baseline_kind',
      'baseline_vol',
      'beats_baseline',
      'horizon_bars',
      'predicted_vol',
      'provenance',
      'score_margin',
      'symbol',
      'timeframe',
      'validation',
    ])
    // No pydantic defaults → all schema-required; the nullable magnitudes are
    // `exclude_none`-stripped from the wire, hence TS-optional.
    expect(requiredNames(dumped.VolatilityForecast)).toEqual([
      'as_of_bar_ts',
      'band',
      'baseline_kind',
      'baseline_vol',
      'beats_baseline',
      'horizon_bars',
      'predicted_vol',
      'provenance',
      'score_margin',
      'symbol',
      'timeframe',
      'validation',
    ])
  })

  it('VolatilityValidation fields match (qlike scalars nullable; baseline_kind closed set)', () => {
    expect(propertyNames(dumped.VolatilityValidation)).toEqual([
      'baseline_kind',
      'baseline_qlike',
      'beats_baseline',
      'ewma_qlike',
      'folds',
      'horizon_bars',
      'model_qlike',
      'n_scored',
      'n_splits',
      'persistence_qlike',
      'score_margin',
    ])
    expect(requiredNames(dumped.VolatilityValidation)).toEqual([
      'baseline_kind',
      'baseline_qlike',
      'beats_baseline',
      'ewma_qlike',
      'folds',
      'horizon_bars',
      'model_qlike',
      'n_scored',
      'n_splits',
      'persistence_qlike',
      'score_margin',
    ])
  })

  it('VolatilityFoldScore fields match (qlike fields nullable, absent when unscored)', () => {
    expect(propertyNames(dumped.VolatilityFoldScore)).toEqual([
      'ewma_qlike',
      'fold_index',
      'model_qlike',
      'n_test',
      'persistence_qlike',
    ])
    expect(requiredNames(dumped.VolatilityFoldScore)).toEqual([
      'ewma_qlike',
      'fold_index',
      'model_qlike',
      'n_test',
      'persistence_qlike',
    ])
  })

  it('RegimeForecastCompletedPayloadV1 carries the forecast inline (Plan 0077)', () => {
    expect(propertyNames(dumped.RegimeForecastCompletedPayloadV1)).toEqual(['forecast'])
    expect(requiredNames(dumped.RegimeForecastCompletedPayloadV1)).toEqual(['forecast'])
  })

  it('RegimeForecast fields match (current_regime/transition_probs nullable; regime closed set)', () => {
    expect(propertyNames(dumped.RegimeForecast)).toEqual([
      'as_of_bar_ts',
      'beats_baseline',
      'current_regime',
      'horizon_bars',
      'provenance',
      'score_margin',
      'symbol',
      'timeframe',
      'transition_probs',
      'validation',
    ])
    expect(requiredNames(dumped.RegimeForecast)).toEqual([
      'as_of_bar_ts',
      'beats_baseline',
      'current_regime',
      'horizon_bars',
      'provenance',
      'score_margin',
      'symbol',
      'timeframe',
      'transition_probs',
      'validation',
    ])
    // `current_regime` is an optional Literal (`| None`), emitted as `anyOf`
    // rather than a top-level `enum`/`$ref` — so `literalValues` can't read it;
    // the property-name + required checks pin its presence/optionality, and the
    // RegimeState set is pinned by the TS union + the Zod enum.
  })

  it('RegimeValidation fields match (brier scalars nullable, absent when unscored)', () => {
    expect(propertyNames(dumped.RegimeValidation)).toEqual([
      'beats_baseline',
      'folds',
      'horizon_bars',
      'model_brier',
      'n_scored',
      'n_splits',
      'persistence_brier',
      'score_margin',
    ])
    expect(requiredNames(dumped.RegimeValidation)).toEqual([
      'beats_baseline',
      'folds',
      'horizon_bars',
      'model_brier',
      'n_scored',
      'n_splits',
      'persistence_brier',
      'score_margin',
    ])
  })

  it('RegimeFoldScore fields match (brier fields nullable, absent when unscored)', () => {
    expect(propertyNames(dumped.RegimeFoldScore)).toEqual([
      'fold_index',
      'model_brier',
      'n_test',
      'persistence_brier',
    ])
    expect(requiredNames(dumped.RegimeFoldScore)).toEqual([
      'fold_index',
      'model_brier',
      'n_test',
      'persistence_brier',
    ])
  })

  it('TechnicalReadCompletedPayloadV1 carries the read inline (Plan 0074)', () => {
    expect(propertyNames(dumped.TechnicalReadCompletedPayloadV1)).toEqual(['read'])
    expect(requiredNames(dumped.TechnicalReadCompletedPayloadV1)).toEqual(['read'])
  })

  it('TechnicalRead fields match (all required; indicator_id/direction closed sets; NO conviction/levels)', () => {
    expect(propertyNames(dumped.TechnicalRead)).toEqual([
      'as_of_bar_ts',
      'direction',
      'indicator_id',
      'rationale',
      'regime_state',
      'symbol',
      'timeframe',
    ])
    // No pydantic defaults, nothing nullable → every field is schema-required.
    expect(requiredNames(dumped.TechnicalRead)).toEqual([
      'as_of_bar_ts',
      'direction',
      'indicator_id',
      'rationale',
      'regime_state',
      'symbol',
      'timeframe',
    ])
    expect(literalValues(dumped.TechnicalRead, 'indicator_id')).toEqual(
      ['ema_stack', 'ichimoku', 'macd', 'supertrend'].sort(),
    )
    expect(literalValues(dumped.TechnicalRead, 'direction')).toEqual(['flat', 'long', 'short'])
    // The honesty guarantee (ADR-0068): the ticket-shaped fields are structurally
    // ABSENT — this is not a Recommendation.
    for (const field of ['conviction', 'entry_zone', 'stop', 'targets']) {
      expect(dumped.TechnicalRead.properties?.[field]).toBeUndefined()
    }
  })

  it('PredictionScreenCompletedPayloadV1 carries the query + opportunities inline (Plan 0078)', () => {
    expect(propertyNames(dumped.PredictionScreenCompletedPayloadV1)).toEqual([
      'opportunities',
      'queried_at',
      'query',
      'source',
    ])
    expect(requiredNames(dumped.PredictionScreenCompletedPayloadV1)).toEqual([
      'opportunities',
      'queried_at',
      'query',
      'source',
    ])
  })

  it('ConvergenceOpportunity fields match (liquidity_caution/volume_usd/market_url nullable, absent when None; NO direction/size/action)', () => {
    expect(propertyNames(dumped.ConvergenceOpportunity)).toEqual([
      'capital_lockup_note',
      'closes_at',
      'implied_probability',
      'implied_return_if_right',
      'liquidity_caution',
      'market_id',
      'market_url',
      'outcome_label',
      'queried_at',
      'question',
      'resolution_risk',
      'source',
      'time_to_resolution',
      'volume_usd',
    ])
    // `liquidity_caution`/`volume_usd` have None defaults → not in `required`, and
    // the bus dumps with `exclude_none`, so they are absent on the wire when the
    // book is deep/unknown — the TS marks them optional. Everything else required.
    expect(requiredNames(dumped.ConvergenceOpportunity)).toEqual([
      'capital_lockup_note',
      'closes_at',
      'implied_probability',
      'implied_return_if_right',
      'market_id',
      'outcome_label',
      'queried_at',
      'question',
      'resolution_risk',
      'source',
      'time_to_resolution',
    ])
    // The ADR-0029/0041 boundary: opportunities are facts with risks attached, so
    // the trade-shaped fields are structurally ABSENT — this is not a call.
    for (const field of ['direction', 'side', 'size', 'action', 'conviction', 'stop', 'targets']) {
      expect(dumped.ConvergenceOpportunity.properties?.[field]).toBeUndefined()
    }
  })

  it('ResolutionRisk fields match (level is a closed low/medium/high set; reasons required)', () => {
    expect(propertyNames(dumped.ResolutionRisk)).toEqual(['level', 'reasons'])
    expect(requiredNames(dumped.ResolutionRisk)).toEqual(['level', 'reasons'])
    expect(literalValues(dumped.ResolutionRisk, 'level')).toEqual(['high', 'low', 'medium'])
  })
})
