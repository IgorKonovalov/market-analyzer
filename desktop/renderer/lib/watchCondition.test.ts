/**
 * Plan 0110 phase 3 — the pure watch-condition formatter.
 *
 * Pinned: the plan's canonical example (`{close, <=, 1831.62}` renders
 * `close ≤ 1831.62`), operator glyph mapping, localized pattern labels
 * (en + ru), strategy id passthrough, and the fall-back-to-slug rule for
 * unknown kinds / malformed params.
 */
import { setLocale } from './i18n'
import { formatWatchCondition } from './watchCondition'

afterEach(() => {
  setLocale('en')
})

describe('indicator_threshold', () => {
  it('renders the plan example: close ≤ 1831.62', () => {
    expect(
      formatWatchCondition('indicator_threshold', {
        indicator: 'close',
        operator: '<=',
        level: 1831.62,
      }),
    ).toBe('close ≤ 1831.62')
  })

  it.each([
    ['<', 'rsi < 30'],
    ['<=', 'rsi ≤ 30'],
    ['>', 'rsi > 30'],
    ['>=', 'rsi ≥ 30'],
  ])('maps operator %s to its display glyph', (operator, expected) => {
    expect(
      formatWatchCondition('indicator_threshold', { indicator: 'rsi', operator, level: 30 }),
    ).toBe(expected)
  })

  it('falls back to the kind slug when params are malformed', () => {
    expect(formatWatchCondition('indicator_threshold', {})).toBe('indicator_threshold')
    expect(
      formatWatchCondition('indicator_threshold', { indicator: 'rsi', operator: '<', level: 'x' }),
    ).toBe('indicator_threshold')
  })
})

describe('pattern', () => {
  it('renders the localized pattern label (en)', () => {
    expect(formatWatchCondition('pattern', { pattern: 'bullish_engulfing' })).toBe(
      'bullish engulfing',
    )
  })

  it('renders the localized pattern label (ru)', () => {
    setLocale('ru')
    expect(formatWatchCondition('pattern', { pattern: 'doji' })).toBe('дожи')
  })

  it('falls back to the raw slug for a pattern outside the glossary', () => {
    expect(formatWatchCondition('pattern', { pattern: 'quadruple_bottom' })).toBe(
      'quadruple_bottom',
    )
  })
})

describe('strategy_signal', () => {
  it('renders the strategy id', () => {
    expect(formatWatchCondition('strategy_signal', { strategy_id: 'rsi_stop', params: {} })).toBe(
      'rsi_stop',
    )
  })
})

it('renders an unknown kind as its slug', () => {
  expect(formatWatchCondition('forecast_probability', { p: 0.7 })).toBe('forecast_probability')
})
