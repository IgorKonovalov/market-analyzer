/**
 * Plan 0091 phase 9 (ADR-0090): the pure divergence-draw core — segment pixel
 * mapping, class×direction colouring, the oscillator→pane-kind mapping, and the
 * hover hit-test. Canvas-free, no chart.
 */
import type { UTCTimestamp } from 'lightweight-charts'

import {
  computeDivergenceSegments,
  divergenceColor,
  divergenceGlossaryKey,
  divergenceLabel,
  divergenceOscillatorToPaneKind,
  fallbackDivergenceColors,
  pointToSegmentDistance,
  requiredOscillatorKindsFor,
} from './divergences'
import type { Divergence } from '../types/events'

const COLORS = fallbackDivergenceColors()

function divergence(overrides: Partial<Divergence> = {}): Divergence {
  return {
    oscillator: 'rsi',
    kind: 'regular_bearish',
    price_pivots: [
      { ts: '2026-05-01T00:00:00Z', price: 120 },
      { ts: '2026-05-10T00:00:00Z', price: 124 },
    ],
    oscillator_pivots: [
      { ts: '2026-05-01T00:00:00Z', price: 78 },
      { ts: '2026-05-10T00:00:00Z', price: 71 },
    ],
    bar_index: 42,
    strength: 0.6,
    ...overrides,
  }
}

// Trivial stub converters: x = seconds/1000, y = price. Keeps the geometry easy to
// assert while exercising the real two-anchor mapping.
const timeToX = (t: UTCTimestamp): number | null => t / 1000
const priceToY = (p: number): number | null => p

describe('computeDivergenceSegments', () => {
  it('maps the PRICE pivots to one segment coloured by kind', () => {
    const segs = computeDivergenceSegments([divergence()], 'price', timeToX, priceToY, COLORS)
    expect(segs).toHaveLength(1)
    expect(segs[0].y1).toBe(120)
    expect(segs[0].y2).toBe(124) // higher high (regular bearish price side)
    expect(segs[0].color).toBe(COLORS.regular_bearish)
  })

  it('maps the OSCILLATOR pivots (values, not price) on the oscillator side', () => {
    const segs = computeDivergenceSegments([divergence()], 'oscillator', timeToX, priceToY, COLORS)
    expect(segs).toHaveLength(1)
    expect(segs[0].y1).toBe(78)
    expect(segs[0].y2).toBe(71) // lower high on the oscillator — the divergence
  })

  it('skips a divergence whose anchor maps off-screen (converter returns null)', () => {
    const offScreen = (): number | null => null
    expect(computeDivergenceSegments([divergence()], 'price', offScreen, priceToY, COLORS)).toEqual(
      [],
    )
  })

  it('a hidden-bullish divergence draws in its own distinct colour', () => {
    const segs = computeDivergenceSegments(
      [divergence({ kind: 'hidden_bullish' })],
      'price',
      timeToX,
      priceToY,
      COLORS,
    )
    expect(segs[0].color).toBe(COLORS.hidden_bullish)
    expect(segs[0].color).not.toBe(COLORS.regular_bearish)
  })
})

describe('divergenceColor / label / glossary key', () => {
  it('gives each of the four kinds a distinct hue', () => {
    const hues = new Set([
      divergenceColor('regular_bearish', COLORS),
      divergenceColor('regular_bullish', COLORS),
      divergenceColor('hidden_bearish', COLORS),
      divergenceColor('hidden_bullish', COLORS),
    ])
    expect(hues.size).toBe(4)
  })

  it('labels + glossary keys are per-kind', () => {
    expect(divergenceLabel('regular_bearish')).toBe('Regular bearish divergence')
    expect(divergenceGlossaryKey('hidden_bullish')).toBe('divergence_hidden_bullish')
  })
})

describe('divergenceOscillatorToPaneKind', () => {
  it('routes macd_hist to the macd pane and keeps the rest', () => {
    expect(divergenceOscillatorToPaneKind('macd_hist')).toBe('macd')
    expect(divergenceOscillatorToPaneKind('rsi')).toBe('rsi')
    expect(divergenceOscillatorToPaneKind('mfi')).toBe('mfi')
    expect(divergenceOscillatorToPaneKind('obv')).toBe('obv')
  })
})

describe('requiredOscillatorKindsFor', () => {
  it('collects the oscillator panes to ensure, excluding obv (the base pane)', () => {
    const kinds = requiredOscillatorKindsFor([
      divergence({ oscillator: 'rsi' }),
      divergence({ oscillator: 'macd_hist' }),
      divergence({ oscillator: 'obv' }),
      divergence({ oscillator: 'rsi' }), // dedup
    ])
    expect([...kinds].sort()).toEqual(['macd', 'rsi'])
  })
})

describe('pointToSegmentDistance (hover hit-test)', () => {
  it('measures perpendicular distance, clamped to the endpoints', () => {
    // Segment (0,0)-(10,0): a point above the middle measures its height.
    expect(pointToSegmentDistance(5, 3, 0, 0, 10, 0)).toBeCloseTo(3, 6)
    // Off the end clamps to the nearer endpoint.
    expect(pointToSegmentDistance(-4, 0, 0, 0, 10, 0)).toBeCloseTo(4, 6)
  })
})
