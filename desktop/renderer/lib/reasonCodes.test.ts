/**
 * Plan 0069 phase 5: reason-code localization.
 *
 * Defends the renderer's half of the ADR-0063 contract — the sidecar ships
 * `{code, params}` facts, the renderer owns the wording: enum tokens map through
 * the enum-label catalog (never a prose-parse), numbers format `en-US`, and the
 * two optional-clause codes drop their clause (not leak a `{param}`) when the
 * value is absent. `en` is the default/test locale, so these assert the English
 * rendering; the Russian catalog + parity land in phase 6.
 */
import { enumLabel, localizeReasonCode } from './reasonCodes'

describe('enumLabel', () => {
  it('resolves closed condition/signal enum tokens', () => {
    expect(enumLabel('trend', 'up')).toBe('up')
    expect(enumLabel('momentum', 'overbought')).toBe('overbought')
    expect(enumLabel('volume', 'heavy')).toBe('heavy')
    expect(enumLabel('direction', 'bullish')).toBe('bullish')
    expect(enumLabel('position', 'long')).toBe('long')
  })

  it('resolves candlestick pattern names to their spaced labels', () => {
    expect(enumLabel('pattern', 'hanging_man')).toBe('hanging man')
    expect(enumLabel('pattern', 'three_white_soldiers')).toBe('three white soldiers')
  })

  it('resolves the three passthrough enums, normalizing spaced/cased upstream tokens', () => {
    expect(enumLabel('edge_strength', 'no_edge')).toBe('no edge over baseline')
    expect(enumLabel('crypto_regime', 'risk_off_structure')).toBe('risk-off structure')
    // The upstream Fear & Greed value arrives spaced + cased.
    expect(enumLabel('fear_greed', 'Extreme Fear')).toBe('Extreme Fear')
  })

  it('falls back to the raw token — never the dotted key — for an unmapped value', () => {
    expect(enumLabel('trend', 'diagonal')).toBe('diagonal')
    expect(enumLabel('pattern', 'not_a_pattern')).toBe('not_a_pattern')
  })
})

describe('localizeReasonCode', () => {
  it('maps the reason.conditions enum params through the catalog (no raw enum key leaks)', () => {
    const line = localizeReasonCode({
      code: 'reason.conditions',
      params: { trend: 'up', momentum: 'bullish', volume: 'heavy' },
    })
    expect(line).toBe('conditions: trend=up, momentum=bullish, volume=heavy')
    expect(line).not.toMatch(/enum\.|\{/)
  })

  it('formats numeric params en-US and includes the optional skill clause when present', () => {
    const line = localizeReasonCode({
      code: 'reason.forecast',
      params: {
        direction: 'long',
        prob: 0.6,
        horizon_bars: 1,
        edge_strength: 'clear',
        skill: 0.61,
        baseline: 0.4,
      },
    })
    expect(line).toBe(
      'forecast: P(long)=0.6 over 1 bar(s), clear edge (out-of-sample skill 0.61 vs baseline 0.4)',
    )
  })

  it('drops the optional skill clause when the sidecar omitted skill/baseline', () => {
    const line = localizeReasonCode({
      code: 'reason.forecast',
      params: { direction: 'short', prob: 0.55, horizon_bars: 5, edge_strength: 'marginal' },
    })
    expect(line).toBe('forecast: P(short)=0.55 over 5 bar(s), marginal edge')
    // No leaked `{skill}` / `{baseline}` for the absent params.
    expect(line).not.toMatch(/\{/)
  })

  it('renders a fresh signal vote with the pluralized fresh clause, position mapped', () => {
    expect(
      localizeReasonCode({
        code: 'signal.vote',
        params: { strategy_id: 'rsi', position: 'long', fresh: 1 },
      }),
    ).toBe('rsi: position=long, fresh signal')
    expect(
      localizeReasonCode({
        code: 'signal.vote',
        params: { strategy_id: 'macd', position: 'flat', fresh: 0 },
      }),
    ).toBe('macd: position=flat')
  })

  it('renders a candlestick condition with pattern + direction mapped', () => {
    expect(
      localizeReasonCode({
        code: 'condition.candlestick',
        params: { pattern: 'hammer', direction: 'bullish' },
      }),
    ).toBe('candlestick: hammer (bullish)')
  })

  it('drops the optional sharpe clause on the no-backtested-edge blocker when absent', () => {
    expect(localizeReasonCode({ code: 'blocker.no_backtested_edge', params: {} })).toBe(
      'no backtested edge',
    )
    expect(
      localizeReasonCode({ code: 'blocker.no_backtested_edge', params: { sharpe_mean: -0.4 } }),
    ).toBe('no backtested edge: walk-forward sharpe_mean -0.4')
  })
})
