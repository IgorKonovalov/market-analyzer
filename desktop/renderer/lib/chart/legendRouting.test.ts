/**
 * legendRouting headless spec (Plan 0098 phase 4, ADR-0092). Pins the two-legend
 * routing decisions: a candlestick GROUP id → group toggle, any other id → hide
 * toggle; a candle key → marker highlight, else the trendline primitive.
 */
import { routeLayerHighlight, routeLayerToggle } from './legendRouting'
import { candleGroupLayerId } from '../candleGroups'

describe('routeLayerToggle', () => {
  it('routes a candlestick group row to a group toggle', () => {
    const id = candleGroupLayerId('hammer:bullish')
    expect(routeLayerToggle(id)).toEqual({ kind: 'candleGroup', groupKey: 'hammer:bullish' })
  })

  it('routes any other layer id to a hidden toggle', () => {
    expect(routeLayerToggle('overlay:ema:20')).toEqual({ kind: 'layer', id: 'overlay:ema:20' })
    expect(routeLayerToggle('trendline:support')).toEqual({
      kind: 'layer',
      id: 'trendline:support',
    })
  })
})

describe('routeLayerHighlight', () => {
  const candleKeys = new Set(['hammer:bullish', 'doji:neutral'])

  it('routes a candlestick group key to marker emphasis', () => {
    expect(routeLayerHighlight('hammer:bullish', candleKeys)).toEqual({
      kind: 'candleGroup',
      key: 'hammer:bullish',
    })
  })

  it('routes any non-candle key to the trendline primitive', () => {
    expect(routeLayerHighlight('neckline:up', candleKeys)).toEqual({
      kind: 'trendline',
      key: 'neckline:up',
    })
  })

  it('routes a null hover-out to the trendline primitive (clearing it)', () => {
    expect(routeLayerHighlight(null, candleKeys)).toEqual({ kind: 'trendline', key: null })
  })
})
