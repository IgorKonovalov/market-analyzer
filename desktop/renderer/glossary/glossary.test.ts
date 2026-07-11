/**
 * Plan 0065 phase 1 done-when: the glossary content and its contract.
 *
 * Defends: every record carries a non-empty `term`, a valid `category`, and BOTH
 * hats (`howComputed` + `whatItMeans`) — the shape validator demonstrably REJECTS
 * a record missing either hat; the `formulaAnchor` lives only on the two computed
 * terms the Python accuracy test pins (conviction, edge_strength); the typed
 * `term(key)` accessor resolves a present key and returns `undefined` for an
 * absent one; the full in-scope term set is present (the plan's spot-check); and
 * the `indicator`-category keys equal exactly the frozen feature-name union — the
 * TS-side mirror of phase 3's cross-language bidirectional pin.
 */
import glossaryJson from './glossary.json'
import { glossaryKeys, localize, term, type GlossaryCategory, type GlossaryRecord } from './types'
import { PATTERN_DISPLAY_NAMES } from '../lib/candleGroups'

const CATEGORIES: readonly GlossaryCategory[] = [
  'forecast',
  'recommendation',
  'condition',
  'indicator',
  'overlay',
  'candlestick',
]
const RECORD_KEYS = new Set(['term', 'category', 'howComputed', 'whatItMeans', 'formulaAnchor'])

const rawGlossary = glossaryJson as Record<string, Record<string, unknown>>

/** Validate one record; returns the list of problems (empty = valid). Used both
 * to prove every real record is well-formed and — against synthetic bad input —
 * to prove the validator actually rejects a record missing either hat. */
function validateRecord(value: Record<string, unknown>): string[] {
  const problems: string[] = []
  // Prose fields are locale-keyed `{ en, ru? }` (Plan 0069 phase 3): `en` is
  // mandatory and non-empty; `ru` is optional (authored in phase 6) but, when
  // present, must be a non-empty string; no other locale keys are allowed.
  const localizedProse = (field: string): void => {
    const v = value[field]
    if (typeof v !== 'object' || v === null) {
      problems.push(`${field} must be a localized { en, ru? } object`)
      return
    }
    const obj = v as Record<string, unknown>
    if (typeof obj.en !== 'string' || obj.en.trim() === '') {
      problems.push(`${field}.en must be a non-empty string`)
    }
    if ('ru' in obj && (typeof obj.ru !== 'string' || obj.ru.trim() === '')) {
      problems.push(`${field}.ru, when present, must be a non-empty string`)
    }
    for (const k of Object.keys(obj)) {
      if (k !== 'en' && k !== 'ru') problems.push(`${field} has an unexpected locale ${k}`)
    }
  }
  localizedProse('term')
  localizedProse('howComputed')
  localizedProse('whatItMeans')
  if (!CATEGORIES.includes(value.category as GlossaryCategory)) {
    problems.push(`category must be one of ${CATEGORIES.join(' / ')}`)
  }
  if ('formulaAnchor' in value) {
    const anchor = value.formulaAnchor
    if (typeof anchor !== 'string' || anchor.trim() === '') {
      problems.push('formulaAnchor, when present, must be a non-empty string')
    }
  }
  for (const k of Object.keys(value)) {
    if (!RECORD_KEYS.has(k)) problems.push(`unexpected field ${k}`)
  }
  return problems
}

it('every record carries a non-empty term, a valid category, and both hats', () => {
  const failures: string[] = []
  for (const [key, record] of Object.entries(rawGlossary)) {
    const problems = validateRecord(record)
    if (problems.length > 0) failures.push(`${key}: ${problems.join('; ')}`)
  }
  expect(failures).toEqual([])
})

it('the shape validator rejects a record missing either hat', () => {
  const base: GlossaryRecord = {
    term: { en: 'X' },
    category: 'forecast',
    howComputed: { en: 'computed' },
    whatItMeans: { en: 'meaning' },
  }
  expect(validateRecord({ ...base, howComputed: { en: '' } })).toContain(
    'howComputed.en must be a non-empty string',
  )
  const missingTraderHat: Record<string, unknown> = {
    term: base.term,
    category: base.category,
    howComputed: base.howComputed,
  }
  expect(validateRecord(missingTraderHat)).toContain(
    'whatItMeans must be a localized { en, ru? } object',
  )
})

it('exposes a typed term(key) accessor: present keys resolve, absent keys are undefined', () => {
  const conviction = term('conviction')
  expect(conviction).toBeDefined()
  expect(localize(conviction!.term, 'en')).toBe('Conviction')
  expect(conviction?.category).toBe('recommendation')
  expect(term('no_such_term_key')).toBeUndefined()
})

describe('localize (per-field locale fallback)', () => {
  it('returns the ru text when the field carries a ru translation', () => {
    expect(localize({ en: 'Conviction', ru: 'Уверенность' }, 'ru')).toBe('Уверенность')
  })

  it('falls back to en when ru is absent — never to the key', () => {
    expect(localize({ en: 'Conviction' }, 'ru')).toBe('Conviction')
  })

  it('returns en for the en locale even when ru is present', () => {
    expect(localize({ en: 'Conviction', ru: 'Уверенность' }, 'en')).toBe('Conviction')
  })
})

it('carries a formulaAnchor on exactly the two computed terms the accuracy test pins', () => {
  const anchored = glossaryKeys().filter((key) => term(key)?.formulaAnchor != null)
  expect(anchored.sort()).toEqual(['conviction', 'edge_strength'])
  expect(term('conviction')?.formulaAnchor).toBe('conviction_mapping')
  expect(term('edge_strength')?.formulaAnchor).toBe('edge_margin_threshold')
})

it('resolves the full in-scope term set (the plan spot-check)', () => {
  const required = [
    // derived metrics / verdicts
    'conviction',
    'edge_strength',
    'skill',
    'baseline_skill',
    'prob_up',
    'sharpe_mean',
    'entry_zone',
    'stop',
    'targets',
    // the five fusion legs
    'alignment',
    'conditions',
    'forecast',
    'signal',
    'backtest',
    // condition terms
    'trend',
    'momentum',
    // representative feature-driver indicators used on the Forecast tab
    'rsi_14',
    'mayer_multiple',
    'funding_rate',
  ]
  const missing = required.filter((key) => term(key) === undefined)
  expect(missing).toEqual([])
})

// The frozen feature-name union — FEATURE_NAMES ∪ FEATURE_NAMES_V2 ∪
// FEATURE_NAMES_V2_DEEP from src/market_analyser/forecast/features.py. Phase 3's
// Python test pins this bidirectionally cross-language; this mirror keeps the JS
// suite honest and makes an add/drop fail here too. Edit both when a feature moves.
const EXPECTED_INDICATOR_KEYS = [
  'ret_1',
  'ret_5',
  'rsi_14',
  'macd',
  'macd_signal',
  'macd_hist',
  'bb_pct_b',
  'atr_pct',
  'adx',
  'plus_di',
  'minus_di',
  'supertrend_dir',
  'ema20_dist',
  'ema50_dist',
  'donchian_pos',
  'rel_volume',
  'halving_phase',
  'days_since_halving',
  'mayer_multiple',
  'dist_200w_ma',
  'fng_value',
  'fng_delta_7',
  'btc_dominance',
  'dominance_delta_7',
  'funding_rate',
  'oi_delta_7',
  'mvrv',
]

it('the indicator-category keys equal exactly the frozen feature-name union', () => {
  const indicatorKeys = glossaryKeys()
    .filter((key) => term(key)?.category === 'indicator')
    .sort()
  expect(indicatorKeys).toEqual([...EXPECTED_INDICATOR_KEYS].sort())
})

// The chart overlay-legend vocabulary (Plan 0065 phase 2) — the overlay kinds
// that actually render a legend row today (the OVERLAY_REGISTRY-supported kinds:
// ema / sma / supertrend). rsi/macd/bbands are reserved-but-unsupported
// OverlayKinds the chart logs-and-skips, so they draw no label to explain; a
// later "OHLCV chart controls" followup adds them when they render. A DISTINCT
// category from `indicator` on purpose: chart-legend copy, not a forecast
// feature, so it is deliberately excluded from the phase-3 FEATURE_NAMES pin.
const EXPECTED_OVERLAY_KEYS = ['ema', 'sma', 'supertrend']

it('the overlay-category keys equal exactly the chart overlay-kind vocabulary', () => {
  const overlayKeys = glossaryKeys()
    .filter((key) => term(key)?.category === 'overlay')
    .sort()
  expect(overlayKeys).toEqual([...EXPECTED_OVERLAY_KEYS].sort())
})

it('overlay keys are disjoint from indicator keys (distinct vocabularies)', () => {
  const indicator = new Set(glossaryKeys().filter((key) => term(key)?.category === 'indicator'))
  const overlap = EXPECTED_OVERLAY_KEYS.filter((key) => indicator.has(key))
  expect(overlap).toEqual([])
})

// Plan 0085: the candlestick category is complete and bidirectionally tied to the
// renderer's detector-token → display-name map (the same keys the chart/legend
// emit). Adding a detector without an entry, or a candlestick entry for a token
// the chart never emits, fails here.
it('has a candlestick entry for every detector pattern token, and no extras', () => {
  const tokens = Object.keys(PATTERN_DISPLAY_NAMES)
  const missing = tokens.filter((token) => term(token)?.category !== 'candlestick')
  expect(missing).toEqual([])
  const candlestickKeys = glossaryKeys()
    .filter((key) => term(key)?.category === 'candlestick')
    .sort()
  expect(candlestickKeys).toEqual([...tokens].sort())
})

it('gives each candlestick entry both hats keyed to the wire token', () => {
  const engulfing = term('bullish_engulfing')
  expect(engulfing?.category).toBe('candlestick')
  expect(localize(engulfing!.term, 'en')).toBe('Bullish engulfing')
  expect(localize(engulfing!.howComputed, 'en')).not.toBe('')
  expect(localize(engulfing!.whatItMeans, 'en')).toContain('bullish reversal')
})
