/**
 * Plan 0096 phase 2: the inline-legend live-value builder.
 *
 * Pins that each series-backed row (indicator overlay + OBV) gets its last-bar
 * value, formatted `toFixed(2)`; price-lines and an empty-bars chart get none.
 */
import { buildLegendValues } from './legendValues'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'

function bar(i: number, close: number, volume = 100): Bar {
  return {
    symbol: 'X',
    timeframe: '1d',
    event_ts: new Date(Date.UTC(2026, 0, i + 1)).toISOString(),
    open: close,
    high: close + 1,
    low: close - 1,
    close,
    volume,
    source: 'test',
  }
}

// A rising series so EMA/OBV both resolve to finite last-bar values.
const BARS: Bar[] = Array.from({ length: 30 }, (_, i) => bar(i, 100 + i))

const EMA: OverlaySpec = { kind: 'ema', period: 5 } as OverlaySpec
const R1: OverlaySpec = {
  kind: 'price_line',
  price: 100,
  label: 'R1',
  role: 'resistance',
} as OverlaySpec

const MONEY = /^\d+\.\d{2}$/

it('returns an empty map for an empty-bars chart', () => {
  expect(buildLegendValues([], [EMA], true).size).toBe(0)
})

it('emits the last-bar EMA value keyed by its layer id, formatted toFixed(2)', () => {
  const values = buildLegendValues(BARS, [EMA], false)
  const v = values.get('overlay:ema:5')
  expect(v).toMatch(MONEY)
})

it('emits the OBV value only when hasObv', () => {
  expect(buildLegendValues(BARS, [], true).get('series:obv')).toMatch(MONEY)
  expect(buildLegendValues(BARS, [], false).has('series:obv')).toBe(false)
})

it('skips price-line overlays (they carry a label, not a live value)', () => {
  const values = buildLegendValues(BARS, [R1], false)
  expect(values.size).toBe(0)
})
