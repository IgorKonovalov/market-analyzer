/**
 * Plan 0092 phase 6: the client-side market-structure mirror matches the Python
 * reference (`analysis/structure.py`) — the same HH/HL/LH/LL labeling, structural
 * trend, and BOS/CHoCH events the sidecar test pins, so the client draw agrees with
 * what `analyze_symbol`'s `market_structure` reports (ADR-0084).
 */
import { marketStructure } from './marketStructure'
import type { Bar } from '../types/sidecar/bar'

function iso(i: number): string {
  return `2025-01-${String(i + 1).padStart(2, '0')}T00:00:00+00:00`
}

/** One bar per value: high = v+0.5, low = v-0.5, close = v (matches the Python
 * test_structure fixture builder). */
function barsFromValues(values: number[]): Bar[] {
  return values.map((v, i) => ({
    symbol: 'T',
    timeframe: '1d',
    event_ts: iso(i),
    open: v,
    high: v + 0.5,
    low: v - 0.5,
    close: v,
    volume: 1000,
    source: 'test',
  }))
}

// Ascending swings -> up (mirrors the Python _UPTREND).
const UPTREND = [
  110, 105, 100, 95, 90, 95, 100, 105, 110, 106.25, 102.5, 98.75, 95, 101.25, 107.5, 113.75, 120,
  115, 110, 105, 100, 107.5, 115, 122.5, 130, 125, 120, 115,
]
// Descending swings -> down (_DOWNTREND).
const DOWNTREND = [
  110, 115, 120, 125, 130, 122.5, 115, 107.5, 100, 105, 110, 115, 120, 112.5, 105, 97.5, 90, 95,
  100, 105, 110, 102.5, 95, 87.5, 80, 85, 90, 95,
]
// Mixed HH high + LL low -> range (_RANGE).
const RANGE = [
  108, 106, 104, 102, 100, 102.5, 105, 107.5, 110, 105, 100, 95, 90, 97.5, 105, 112.5, 120, 115,
  110, 105,
]
// Uptrend then breakdown: BOS up at bar 11, CHoCH down at bar 17 (_CHOCH).
const CHOCH = [
  100, 102.5, 105, 107.5, 110, 107.5, 105, 102.5, 100, 104, 108, 112, 116, 112.125, 108.25, 104.375,
  100.5, 96.625, 92.75, 88.875, 85, 90, 95, 100, 105,
]

function labels(bars: Bar[]): string[] {
  return marketStructure(bars).labeledPivots.map((lp) => lp.label)
}

describe('marketStructure', () => {
  it('HH/HL sequence yields up with the right labels', () => {
    const ms = marketStructure(barsFromValues(UPTREND))
    expect(ms.structuralTrend).toBe('up')
    expect(labels(barsFromValues(UPTREND))).toEqual(['HL', 'HH', 'HL', 'HH'])
  })

  it('LH/LL mirror yields down', () => {
    const ms = marketStructure(barsFromValues(DOWNTREND))
    expect(ms.structuralTrend).toBe('down')
    expect(labels(barsFromValues(DOWNTREND))).toEqual(['LH', 'LL', 'LH', 'LL'])
  })

  it('mixed structure yields range', () => {
    const ms = marketStructure(barsFromValues(RANGE))
    expect(ms.structuralTrend).toBe('range')
    expect(labels(barsFromValues(RANGE))).toEqual(['LL', 'HH'])
  })

  it('emits BOS then CHoCH at the correct bars (margin 0)', () => {
    const ms = marketStructure(barsFromValues(CHOCH), 3, 0)
    expect(ms.events).toEqual([
      { kind: 'BOS', direction: 'bullish', barIndex: 11, price: 110.5 },
      { kind: 'CHoCH', direction: 'bearish', barIndex: 17, price: 99.5 },
    ])
  })

  it('is trailing — a read on bars[0..=k] is a prefix of the full-series read', () => {
    const bars = barsFromValues(CHOCH)
    const full = marketStructure(bars, 3, 0)
    for (let k = 1; k <= bars.length; k++) {
      const partial = marketStructure(bars.slice(0, k), 3, 0)
      expect(partial.events).toEqual(full.events.filter((e) => e.barIndex < k))
    }
  })

  it('empty bars is range with no events', () => {
    expect(marketStructure([])).toEqual({ structuralTrend: 'range', labeledPivots: [], events: [] })
  })
})
