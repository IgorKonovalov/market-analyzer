/**
 * Plan 0082 phase 4: the add-indicator form validator (ADR-0077).
 */
import {
  OVERLAY_FORM_KINDS,
  buildOverlayFromForm,
  defaultPeriodFor,
  formKindTakesPeriod,
  formKindTakesStdDev,
} from './overlayForm'

describe('overlay form kinds', () => {
  it('offers only the client-computable overlay-path kinds (no price_line/rsi/macd/vwap/obv)', () => {
    expect([...OVERLAY_FORM_KINDS]).toEqual(['ema', 'sma', 'bbands', 'supertrend', 'ichimoku'])
  })

  it('takes a period for ema/sma/bbands/supertrend, none for ichimoku', () => {
    expect(formKindTakesPeriod('ema')).toBe(true)
    expect(formKindTakesPeriod('supertrend')).toBe(true)
    expect(formKindTakesPeriod('ichimoku')).toBe(false)
  })

  it('takes a std-dev (k) only for bbands', () => {
    expect(formKindTakesStdDev('bbands')).toBe(true)
    expect(formKindTakesStdDev('ema')).toBe(false)
  })
})

describe('buildOverlayFromForm', () => {
  it('builds a period-only spec for ema/sma/supertrend', () => {
    expect(buildOverlayFromForm('ema', 20, 2)).toEqual({
      ok: true,
      spec: { kind: 'ema', period: 20 },
    })
    expect(buildOverlayFromForm('supertrend', 10, 2)).toEqual({
      ok: true,
      spec: { kind: 'supertrend', period: 10 },
    })
  })

  it('builds a period + multiplier(k) spec for bbands', () => {
    expect(buildOverlayFromForm('bbands', 20, 2.5)).toEqual({
      ok: true,
      spec: { kind: 'bbands', period: 20, multiplier: 2.5 },
    })
  })

  it('builds a bare spec for ichimoku (classic defaults, no fields)', () => {
    expect(buildOverlayFromForm('ichimoku', NaN, NaN)).toEqual({
      ok: true,
      spec: { kind: 'ichimoku' },
    })
  })

  it('rejects a non-positive or non-integer period for a period kind', () => {
    expect(buildOverlayFromForm('ema', 0, 2)).toEqual({ ok: false, error: 'period' })
    expect(buildOverlayFromForm('ema', -5, 2)).toEqual({ ok: false, error: 'period' })
    expect(buildOverlayFromForm('ema', 20.5, 2)).toEqual({ ok: false, error: 'period' })
    expect(buildOverlayFromForm('ema', NaN, 2)).toEqual({ ok: false, error: 'period' })
  })

  it('rejects a non-positive std-dev for bbands', () => {
    expect(buildOverlayFromForm('bbands', 20, 0)).toEqual({ ok: false, error: 'stdDev' })
    expect(buildOverlayFromForm('bbands', 20, -1)).toEqual({ ok: false, error: 'stdDev' })
    expect(buildOverlayFromForm('bbands', 20, NaN)).toEqual({ ok: false, error: 'stdDev' })
  })

  it('never emits price/label/role on a built spec', () => {
    const result = buildOverlayFromForm('bbands', 20, 2)
    expect(result.ok).toBe(true)
    if (result.ok) {
      expect('price' in result.spec).toBe(false)
      expect('label' in result.spec).toBe(false)
      expect('role' in result.spec).toBe(false)
    }
  })

  it('gives sane default periods per kind', () => {
    expect(defaultPeriodFor('ema')).toBe(20)
    expect(defaultPeriodFor('sma')).toBe(50)
    expect(defaultPeriodFor('supertrend')).toBe(10)
  })
})
