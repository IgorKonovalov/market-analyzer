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
  type?: string | string[]
  anyOf?: JsonSchema[]
  $ref?: string
  $defs?: Record<string, JsonSchema>
  items?: JsonSchema
}

interface DumpedSchemas {
  OverlaySpec: JsonSchema
  Marker: JsonSchema
  ChartShowPayloadV1: JsonSchema
  ChartUpdatePayloadV1: JsonSchema
  ChartHighlightPayloadV1: JsonSchema
  RunCompletedPayloadV1: JsonSchema
  GapWindow: JsonSchema
  OhlcvBackfillStartedPayloadV1: JsonSchema
  OhlcvBackfilledPayloadV1: JsonSchema
  OhlcvBackfillFailedPayloadV1: JsonSchema
}

const REPO_ROOT = resolve(__dirname, '..', '..', '..')

function dumpPydanticSchemas(): DumpedSchemas {
  const script = [
    'import json',
    'from market_analyser.api.events import (',
    '    OverlaySpec, Marker,',
    '    ChartShowPayloadV1, ChartUpdatePayloadV1,',
    '    ChartHighlightPayloadV1, RunCompletedPayloadV1,',
    '    GapWindow, OhlcvBackfillStartedPayloadV1,',
    '    OhlcvBackfilledPayloadV1, OhlcvBackfillFailedPayloadV1,',
    ')',
    'print(json.dumps({',
    '    "OverlaySpec": OverlaySpec.model_json_schema(),',
    '    "Marker": Marker.model_json_schema(),',
    '    "ChartShowPayloadV1": ChartShowPayloadV1.model_json_schema(),',
    '    "ChartUpdatePayloadV1": ChartUpdatePayloadV1.model_json_schema(),',
    '    "ChartHighlightPayloadV1": ChartHighlightPayloadV1.model_json_schema(),',
    '    "RunCompletedPayloadV1": RunCompletedPayloadV1.model_json_schema(),',
    '    "GapWindow": GapWindow.model_json_schema(),',
    '    "OhlcvBackfillStartedPayloadV1": OhlcvBackfillStartedPayloadV1.model_json_schema(),',
    '    "OhlcvBackfilledPayloadV1": OhlcvBackfilledPayloadV1.model_json_schema(),',
    '    "OhlcvBackfillFailedPayloadV1": OhlcvBackfillFailedPayloadV1.model_json_schema(),',
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

  it('OverlaySpec fields match', () => {
    expect(propertyNames(dumped.OverlaySpec)).toEqual(['kind', 'period'])
    expect(requiredNames(dumped.OverlaySpec)).toEqual(['kind'])
    expect(literalValues(dumped.OverlaySpec, 'kind')).toEqual(
      ['bbands', 'ema', 'macd', 'rsi', 'sma'].sort(),
    )
  })

  it('Marker fields match', () => {
    expect(propertyNames(dumped.Marker)).toEqual(['event_ts', 'kind', 'label'])
    expect(requiredNames(dumped.Marker)).toEqual(['event_ts', 'kind'])
    expect(literalValues(dumped.Marker, 'kind')).toEqual(
      ['bearish_marker', 'bullish_marker'].sort(),
    )
  })

  it('ChartShowPayloadV1 fields match', () => {
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

  it('ChartUpdatePayloadV1 fields match (all optional except symbol+timeframe)', () => {
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
})
