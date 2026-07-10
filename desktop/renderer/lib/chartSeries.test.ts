import {
  chartColorsFrom,
  mainSeriesKind,
  overlayKey,
  overlayStyleColor,
  overlayStyleWidth,
  type ChartColors,
} from './chartSeries'
import type { ResolvedChartStyle } from './chartStyle'
import type { OverlaySpec } from '../types/events'

describe('mainSeriesKind', () => {
  it('maps each candle-series type to its test-hook kind', () => {
    expect(mainSeriesKind('candles')).toBe('candlestick')
    expect(mainSeriesKind('bars')).toBe('bar')
    expect(mainSeriesKind('line')).toBe('line')
    expect(mainSeriesKind('area')).toBe('area')
  })
})

describe('overlayKey', () => {
  it('keys by kind and period', () => {
    expect(overlayKey({ kind: 'ema', period: 20 } as OverlaySpec)).toBe('ema:20')
  })

  it('uses "na" for a periodless overlay', () => {
    expect(overlayKey({ kind: 'price_line', price: 100 } as OverlaySpec)).toBe('price_line:na')
  })
})

// A minimal resolved style: `colors` carries the styleable elements, `chrome`
// the non-overridable rest. chartColorsFrom flattens the two.
const STYLE = {
  colors: {
    candleUp: '#0a0',
    candleDown: '#a00',
    volume: '#888',
    volumeMa: '#88a',
    vwap: '#a8a',
    obv: '#8aa',
    markerBullish: '#0b0',
    markerBearish: '#b00',
    markerNeutral: '#999',
    ema: '#00f',
    sma: '#f0f',
  },
  chrome: {
    text: '#111',
    border: '#ccc',
    markerClicked: '#333',
  },
  widths: {
    volumeMa: 1,
    vwap: 2,
    obv: 1,
    ema: 3,
    sma: 4,
  },
} as unknown as ResolvedChartStyle

describe('chartColorsFrom', () => {
  it('merges the styleable colours and the chrome into one flat view', () => {
    const flat: ChartColors = chartColorsFrom(STYLE)
    expect(flat.candleUp).toBe('#0a0') // from colors
    expect(flat.text).toBe('#111') // from chrome
    expect(flat.markerClicked).toBe('#333') // from chrome
    expect(flat.vwap).toBe('#a8a') // from colors
  })
})

describe('overlayStyleColor / overlayStyleWidth', () => {
  it('reads the styleable entry for an ema/sma overlay (honouring the override)', () => {
    const ema = { kind: 'ema', period: 20 } as OverlaySpec
    expect(overlayStyleColor(ema, STYLE)).toBe('#00f')
    expect(overlayStyleWidth(ema, STYLE)).toBe(3)
  })

  it('falls back to the registry colour and the default width for a non-styleable kind', () => {
    const st = { kind: 'supertrend', period: 10 } as OverlaySpec
    // Not ema/sma → registry colour (not a style entry) + the default overlay width.
    expect(overlayStyleColor(st, STYLE)).not.toBe('#00f')
    expect(overlayStyleWidth(st, STYLE)).toBe(2)
  })
})
