/**
 * Plan 0105 phase 4 done-when (the headless-testable part): the pane-id → label
 * mapping is pinned for every managed sub-pane kind, and the label primitive
 * actually draws its text. Positioning across pane resize / chart rebuild is not
 * jsdom-testable (no canvas layout) — the phase-8 live smoke covers that.
 */
import { OSCILLATOR_KINDS } from './overlays'
import { PaneLabelPrimitive, paneLabelFor } from './paneLabel'

describe('paneLabelFor', () => {
  it('pins the short name of every managed sub-pane kind', () => {
    expect(paneLabelFor('obv')).toBe('OBV')
    expect(paneLabelFor('stochastic')).toBe('Stochastic')
    expect(paneLabelFor('stoch_rsi')).toBe('Stoch RSI')
    expect(paneLabelFor('cci')).toBe('CCI')
    expect(paneLabelFor('williams_r')).toBe('Williams %R')
    expect(paneLabelFor('roc')).toBe('ROC')
    expect(paneLabelFor('mfi')).toBe('MFI')
    expect(paneLabelFor('cmf')).toBe('CMF')
    expect(paneLabelFor('ad_line')).toBe('A/D line')
    expect(paneLabelFor('rsi')).toBe('RSI')
    expect(paneLabelFor('macd')).toBe('MACD hist')
  })

  it('covers every oscillator-pane kind (no pane renders a raw token)', () => {
    for (const kind of OSCILLATOR_KINDS) {
      const label = paneLabelFor(kind)
      expect(label).not.toBe('')
      // An authored short name never carries the raw token's underscore — an
      // upper-cased `STOCH_RSI` here would mean the kind fell out of the map.
      expect(label).not.toContain('_')
    }
  })

  it('humanises an unknown kind instead of dropping the label', () => {
    expect(paneLabelFor('bbands')).toBe('BBANDS')
  })
})

describe('PaneLabelPrimitive', () => {
  it('draws its text at the pane top-left via the media coordinate space', () => {
    const primitive = new PaneLabelPrimitive('Williams %R')
    const views = primitive.paneViews()
    expect(views).toHaveLength(1)

    const fillText = jest.fn()
    const ctx = {
      save: jest.fn(),
      restore: jest.fn(),
      fillText,
    } as unknown as CanvasRenderingContext2D
    const target = {
      useMediaCoordinateSpace: (cb: (scope: { context: CanvasRenderingContext2D }) => void) =>
        cb({ context: ctx }),
    }
    views[0].renderer()?.draw(target as never)
    expect(fillText).toHaveBeenCalledWith('Williams %R', expect.any(Number), expect.any(Number))
  })
})
