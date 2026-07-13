/**
 * Plan 0096 phase 3: the persisted layer-visibility store (ADR-0089).
 *
 * Pins the "Clean on open" default (only OBV hidden for an unstored bucket),
 * that a toggle persists an explicit set seeded from that default, and that
 * setLayerVisibility replaces a bucket and round-trips through localStorage.
 * Distinct buckets per test avoid the module-singleton bleeding between cases.
 */
import {
  DEFAULT_HIDDEN,
  getLayerVisibilitySnapshot,
  hiddenForBucket,
  layerVisibilityStoreKey,
  setLayerVisibility,
  toggleLayerVisibility,
} from './layerVisibility'
import { MARKET_STRUCTURE_LAYER_ID, OBV_LAYER_ID } from './chartSeries'

afterEach(() => {
  try {
    window.localStorage.clear()
  } catch {
    /* ignore */
  }
})

it('an unstored bucket defaults to Clean — OBV and market structure hidden', () => {
  const hidden = hiddenForBucket(getLayerVisibilitySnapshot(), 'AAA-USD', '1d')
  expect(hidden.has(OBV_LAYER_ID)).toBe(true)
  expect(hidden.has(MARKET_STRUCTURE_LAYER_ID)).toBe(true)
  expect(hidden.size).toBe(2)
  expect(DEFAULT_HIDDEN.has(OBV_LAYER_ID)).toBe(true)
})

it('toggling a layer persists an explicit set seeded from the default', () => {
  toggleLayerVisibility('BBB-USD', '1d', 'overlay:ema:20')
  const hidden = hiddenForBucket(getLayerVisibilitySnapshot(), 'BBB-USD', '1d')
  expect(hidden.has('overlay:ema:20')).toBe(true)
  // Seeded from DEFAULT_HIDDEN, so OBV is still hidden.
  expect(hidden.has(OBV_LAYER_ID)).toBe(true)
})

it('toggling OBV out of the default set makes it visible', () => {
  toggleLayerVisibility('CCC-USD', '1d', OBV_LAYER_ID)
  const hidden = hiddenForBucket(getLayerVisibilitySnapshot(), 'CCC-USD', '1d')
  expect(hidden.has(OBV_LAYER_ID)).toBe(false)
})

it('setLayerVisibility replaces a bucket and round-trips through localStorage', () => {
  setLayerVisibility('DDD-USD', '1d', new Set(['series:obv', 'overlay:sma:50']))
  const hidden = hiddenForBucket(getLayerVisibilitySnapshot(), 'DDD-USD', '1d')
  expect([...hidden].sort()).toEqual(['overlay:sma:50', 'series:obv'])
  const raw = JSON.parse(window.localStorage.getItem('ma.layerVisibility') ?? '{}') as Record<
    string,
    string[]
  >
  expect(new Set(raw[layerVisibilityStoreKey('DDD-USD', '1d')])).toEqual(
    new Set(['series:obv', 'overlay:sma:50']),
  )
})
