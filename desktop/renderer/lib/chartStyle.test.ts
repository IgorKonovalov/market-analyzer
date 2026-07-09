/**
 * Chart-style store unit tests (Plan 0068 phase 1, ADR-0062).
 *
 * jsdom provides localStorage + getComputedStyle but does not load styles.css, so
 * the theme default tokens are unset and resolve to the light fallbacks. Where a
 * test needs to prove the resolver reads a *DOM token* as the base colour, it
 * stubs `getComputedStyle` to return one (the same technique the chart's
 * theme-recolor spec uses).
 */
import {
  CHART_STYLE_ELEMENTS,
  MAX_LINE_WIDTH,
  getCandleType,
  getChartStyleOverrides,
  resetChartStyle,
  resolveChartStyle,
  setCandleType,
  setElementOverride,
  subscribeChartStyle,
} from './chartStyle'

const realGetComputedStyle = window.getComputedStyle.bind(window)

/** Stub `getComputedStyle` so the named custom properties resolve to the given
 * values (everything else defers to the real, empty jsdom declaration). */
function stubTokens(tokens: Record<string, string>): void {
  jest.spyOn(window, 'getComputedStyle').mockImplementation(((
    el: Element,
    pseudo?: string | null,
  ) => {
    const decl = realGetComputedStyle(el, pseudo ?? undefined)
    const orig = decl.getPropertyValue.bind(decl)
    decl.getPropertyValue = (prop: string): string => tokens[prop] ?? orig(prop)
    return decl
  }) as typeof window.getComputedStyle)
}

function container(): HTMLElement {
  return document.createElement('div')
}

beforeEach(() => {
  resetChartStyle()
  window.localStorage.clear()
})

afterEach(() => {
  jest.restoreAllMocks()
})

describe('resolveChartStyle — no overrides (done-when a)', () => {
  it('returns the theme default tokens, default widths, and candleType "candles"', () => {
    stubTokens({ '--chart-up': '#001100', '--overlay-ema': '#002200' })
    const resolved = resolveChartStyle(container(), 'light')

    // A stubbed DOM token flows through as the base colour…
    expect(resolved.colors.candleUp).toBe('#001100')
    expect(resolved.colors.ema).toBe('#002200')
    // …and an unset token falls back to the light default.
    expect(resolved.colors.candleDown).toBe('#b42318')

    // Default widths are the old hard-coded literals.
    expect(resolved.widths).toEqual({ volumeMa: 1, vwap: 2, obv: 1, ema: 2, sma: 2 })
    expect(resolved.candleType).toBe('candles')
  })

  it('resolves every styleable element and the non-overridable chrome', () => {
    const resolved = resolveChartStyle(container(), 'dark')
    for (const element of CHART_STYLE_ELEMENTS) {
      expect(typeof resolved.colors[element]).toBe('string')
      expect(resolved.colors[element].length).toBeGreaterThan(0)
    }
    expect(resolved.chrome).toEqual({
      text: '#1a1a1a',
      border: '#e5e5e5',
      markerClicked: '#2563eb',
    })
  })
})

describe('per-theme overrides (done-when b)', () => {
  it('a colour override wins for its theme and leaves the other theme untouched', () => {
    setElementOverride('dark', 'candleUp', { color: '#abcdef' })

    expect(resolveChartStyle(container(), 'dark').colors.candleUp).toBe('#abcdef')
    // Light is independent — still the default (no styles.css token in jsdom).
    expect(resolveChartStyle(container(), 'light').colors.candleUp).toBe('#15803d')
  })

  it('a width override wins for its theme only', () => {
    setElementOverride('light', 'ema', { lineWidth: 4 })

    expect(resolveChartStyle(container(), 'light').widths.ema).toBe(4)
    expect(resolveChartStyle(container(), 'dark').widths.ema).toBe(2)
  })

  it('clamps an out-of-range width into [1, MAX]', () => {
    setElementOverride('light', 'vwap', { lineWidth: 99 })
    expect(resolveChartStyle(container(), 'light').widths.vwap).toBe(MAX_LINE_WIDTH)
    setElementOverride('light', 'vwap', { lineWidth: 0 })
    expect(resolveChartStyle(container(), 'light').widths.vwap).toBe(1)
  })

  it('merges successive patches for the same element (colour then width)', () => {
    setElementOverride('dark', 'sma', { color: '#123456' })
    setElementOverride('dark', 'sma', { lineWidth: 3 })
    const resolved = resolveChartStyle(container(), 'dark')
    expect(resolved.colors.sma).toBe('#123456')
    expect(resolved.widths.sma).toBe(3)
  })
})

describe('candle-type + reset round-trip through storage (done-when c)', () => {
  it('setCandleType persists and resolves', () => {
    setCandleType('line')
    expect(getCandleType()).toBe('line')
    expect(resolveChartStyle(container(), 'light').candleType).toBe('line')
    expect(JSON.parse(window.localStorage.getItem('ma.chartStyle') as string).candleType).toBe(
      'line',
    )
  })

  it('resetChartStyle clears every override back to defaults', () => {
    setElementOverride('dark', 'candleUp', { color: '#abcdef', lineWidth: 4 })
    setCandleType('area')

    resetChartStyle()

    expect(getChartStyleOverrides()).toEqual({ light: {}, dark: {} })
    expect(getCandleType()).toBe('candles')
    const resolved = resolveChartStyle(container(), 'dark')
    expect(resolved.colors.candleUp).toBe('#15803d')
    expect(resolved.candleType).toBe('candles')
    // Storage reflects the cleared model.
    expect(JSON.parse(window.localStorage.getItem('ma.chartStyle') as string)).toEqual({
      light: {},
      dark: {},
    })
  })

  it('a stored override rehydrates on a fresh module load', async () => {
    window.localStorage.setItem(
      'ma.chartStyle',
      JSON.stringify({
        light: { obv: { color: '#0f0f0f', lineWidth: 3 } },
        dark: {},
        candleType: 'bars',
      }),
    )
    jest.resetModules()
    const fresh = await import('./chartStyle')
    const resolved = fresh.resolveChartStyle(container(), 'light')
    expect(resolved.colors.obv).toBe('#0f0f0f')
    expect(resolved.widths.obv).toBe(3)
    expect(resolved.candleType).toBe('bars')
  })
})

describe('malformed / blocked storage degrades to defaults (done-when d)', () => {
  it('malformed JSON in storage loads as empty defaults without throwing', async () => {
    window.localStorage.setItem('ma.chartStyle', '{ not json ]')
    jest.resetModules()
    const fresh = await import('./chartStyle')
    expect(fresh.getChartStyleOverrides()).toEqual({ light: {}, dark: {} })
    expect(() => fresh.resolveChartStyle(container(), 'dark')).not.toThrow()
  })

  it('a well-formed-but-garbage shape is sanitised to defaults', async () => {
    window.localStorage.setItem(
      'ma.chartStyle',
      JSON.stringify({
        light: 'nope',
        dark: { candleUp: 42, bogus: { color: 1 } },
        candleType: 'spiral',
      }),
    )
    jest.resetModules()
    const fresh = await import('./chartStyle')
    expect(fresh.getChartStyleOverrides()).toEqual({ light: {}, dark: {} })
    expect(fresh.getCandleType()).toBe('candles')
  })

  it('a blocked localStorage.getItem loads defaults without throwing', async () => {
    jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    jest.resetModules()
    const fresh = await import('./chartStyle')
    expect(fresh.getChartStyleOverrides()).toEqual({ light: {}, dark: {} })
  })

  it('a blocked localStorage.setItem still applies the override in memory', () => {
    jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    expect(() => setElementOverride('light', 'vwap', { color: '#777777' })).not.toThrow()
    expect(resolveChartStyle(container(), 'light').colors.vwap).toBe('#777777')
  })
})

describe('subscribeChartStyle (done-when e)', () => {
  it('fires on every mutator and stops after unsubscribe', () => {
    const cb = jest.fn()
    const unsub = subscribeChartStyle(cb)

    setElementOverride('light', 'ema', { color: '#111111' })
    setCandleType('area')
    resetChartStyle()
    expect(cb).toHaveBeenCalledTimes(3)

    unsub()
    setCandleType('line')
    expect(cb).toHaveBeenCalledTimes(3)
  })
})
