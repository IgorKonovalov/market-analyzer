/**
 * Plan 0052 phase 4: the pure trendline helpers + primitive in
 * `lib/trendlines.ts`.
 *
 * `computeTrendlineSegments` maps anchor pairs to pixel segments via stubbed
 * time→x / price→y converters, skipping a segment whose either endpoint maps
 * off-screen and colouring by PATTERN-TYPE token (Plan 0067 / ADR-0061) —
 * canvas-free, the `spans.test.ts` precedent. `dedupeTrendlines` collapses the
 * forming(dashed)+confirmed(solid) twin. The primitive tests pin the
 * forming/confirmed styling (dashed vs solid on its segment state) and the
 * empty-until-attached / visibility behaviour.
 */
import type { SeriesAttachedParameter, Time, UTCTimestamp } from 'lightweight-charts'

import type { TrendlineSpec } from '../types/events'
import {
  TRENDLINE_HIT_TOLERANCE_PX,
  TrendlinePrimitive,
  computeTrendlineSegments,
  dedupeTrendlines,
  nearestTrendlineGroup,
  patternDisplayName,
  patternStateKey,
  pointSegmentDistance,
  resolveTimeX,
  timeToFractionalLogical,
  trendlineColor,
  trendlineGroupLayerId,
  trendlineStateLabel,
  type TrendlineColors,
  type TrendlineSegment,
} from './trendlines'

const COLORS: TrendlineColors = {
  head_shoulders: '#111111',
  inverse_head_shoulders: '#222222',
  double_top: '#333333',
  double_bottom: '#444444',
  ascending_triangle: '#555555',
  descending_triangle: '#666666',
  symmetrical_triangle: '#777777',
  rising_wedge: '#888888',
  falling_wedge: '#999999',
  neutral: '#aaaaaa',
}

const T1 = '2026-05-13T00:00:00Z'
const T2 = '2026-05-15T00:00:00Z'
const T3 = '2026-05-17T00:00:00Z'

const toUtc = (iso: string): UTCTimestamp =>
  Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp

/** time→x stub: T1→100, T2→160, T3→220; anything else off-screen. */
const timeToX = (t: UTCTimestamp): number | null =>
  t === toUtc(T1) ? 100 : t === toUtc(T2) ? 160 : t === toUtc(T3) ? 220 : null

/** price→y stub: linear 10→80, 20→40 (y = 120 - 4 * price); negative = off-screen. */
const priceToY = (price: number): number | null => (price < 0 ? null : 120 - 4 * price)

const NECKLINE: TrendlineSpec = {
  points: [
    { ts: T1, price: 10 },
    { ts: T2, price: 20 },
  ],
  role: 'neckline',
  style: 'solid',
  pattern: 'head_shoulders',
}

describe('computeTrendlineSegments', () => {
  it('maps a two-anchor spec to the expected (x1,y1)-(x2,y2) via the stubbed scales', () => {
    const segments = computeTrendlineSegments([NECKLINE], timeToX, priceToY, COLORS)
    expect(segments).toHaveLength(1)
    expect(segments[0]).toMatchObject({ x1: 100, y1: 80, x2: 160, y2: 40 })
  })

  it('maps a three-anchor polyline to two consecutive segments', () => {
    const spec: TrendlineSpec = {
      points: [
        { ts: T1, price: 10 },
        { ts: T2, price: 20 },
        { ts: T3, price: 15 },
      ],
      style: 'solid',
    }
    const segments = computeTrendlineSegments([spec], timeToX, priceToY, COLORS)
    expect(segments).toHaveLength(2)
    expect(segments[0]).toMatchObject({ x1: 100, y1: 80, x2: 160, y2: 40 })
    expect(segments[1]).toMatchObject({ x1: 160, y1: 40, x2: 220, y2: 60 })
  })

  it('skips a segment whose TIME endpoint maps off-screen (converter returns null)', () => {
    const offscreen: TrendlineSpec = {
      ...NECKLINE,
      points: [
        { ts: '2020-01-01T00:00:00Z', price: 10 }, // unknown to the stub → null
        { ts: T2, price: 20 },
      ],
    }
    expect(computeTrendlineSegments([offscreen], timeToX, priceToY, COLORS)).toEqual([])
  })

  it('skips a segment whose PRICE endpoint maps off-screen (converter returns null)', () => {
    const offscreen: TrendlineSpec = {
      ...NECKLINE,
      points: [
        { ts: T1, price: -5 }, // negative → null in the stub
        { ts: T2, price: 20 },
      ],
    }
    expect(computeTrendlineSegments([offscreen], timeToX, priceToY, COLORS)).toEqual([])
  })

  it('flags dashed for a forming (style="dashed") spec and not for a confirmed one', () => {
    const forming: TrendlineSpec = { ...NECKLINE, style: 'dashed' }
    const confirmed: TrendlineSpec = { ...NECKLINE, style: 'solid' }
    const segments = computeTrendlineSegments([forming, confirmed], timeToX, priceToY, COLORS)
    expect(segments.map((s) => s.dashed)).toEqual([true, false])
  })

  it('colours by pattern type: a distinct colour per type, neutral for unknown/absent', () => {
    expect(trendlineColor('head_shoulders', COLORS)).toBe(COLORS.head_shoulders)
    expect(trendlineColor('symmetrical_triangle', COLORS)).toBe(COLORS.symmetrical_triangle)
    expect(trendlineColor('falling_wedge', COLORS)).toBe(COLORS.falling_wedge)
    // Every known type maps to its own hue (no two collide).
    const known = [
      'head_shoulders',
      'inverse_head_shoulders',
      'double_top',
      'double_bottom',
      'ascending_triangle',
      'descending_triangle',
      'symmetrical_triangle',
      'rising_wedge',
      'falling_wedge',
    ]
    expect(new Set(known.map((p) => trendlineColor(p, COLORS))).size).toBe(known.length)
    // Unknown / absent pattern → the stable neutral hue.
    expect(trendlineColor('not_a_pattern', COLORS)).toBe(COLORS.neutral)
    expect(trendlineColor(null, COLORS)).toBe(COLORS.neutral)
    expect(trendlineColor(undefined, COLORS)).toBe(COLORS.neutral)

    // A spec's drawn segment carries its pattern colour, regardless of role.
    const segments = computeTrendlineSegments(
      [{ ...NECKLINE, pattern: 'double_bottom', role: 'lower_trendline' }],
      timeToX,
      priceToY,
      COLORS,
    )
    expect(segments[0].color).toBe(COLORS.double_bottom)
  })
})

describe('dedupeTrendlines', () => {
  const geom = [
    { ts: T1, price: 10 },
    { ts: T2, price: 20 },
  ]

  it('drops a dashed spec whose points match a solid spec of the same pattern (keeps the solid)', () => {
    const solid: TrendlineSpec = { points: geom, style: 'solid', pattern: 'rising_wedge' }
    const dashedTwin: TrendlineSpec = { points: geom, style: 'dashed', pattern: 'rising_wedge' }
    const out = dedupeTrendlines([dashedTwin, solid])
    expect(out).toEqual([solid])
  })

  it('leaves a forming-only (dashed, no solid twin) spec intact', () => {
    const formingOnly: TrendlineSpec = { points: geom, style: 'dashed', pattern: 'double_top' }
    expect(dedupeTrendlines([formingOnly])).toEqual([formingOnly])
  })

  it('leaves a confirmed-only (solid) spec intact', () => {
    const confirmedOnly: TrendlineSpec = { points: geom, style: 'solid', pattern: 'double_top' }
    expect(dedupeTrendlines([confirmedOnly])).toEqual([confirmedOnly])
  })

  it('does NOT collapse a dashed spec of DIFFERENT geometry from the solid', () => {
    const solid: TrendlineSpec = { points: geom, style: 'solid', pattern: 'rising_wedge' }
    // Same pattern, different anchor prices → distinct geometry, not a twin.
    const otherDashed: TrendlineSpec = {
      points: [
        { ts: T1, price: 11 },
        { ts: T2, price: 21 },
      ],
      style: 'dashed',
      pattern: 'rising_wedge',
    }
    const out = dedupeTrendlines([solid, otherDashed])
    expect(out).toEqual([solid, otherDashed])
  })

  it('preserves order of the surviving specs', () => {
    const a: TrendlineSpec = { points: geom, style: 'solid', pattern: 'head_shoulders' }
    const b: TrendlineSpec = {
      points: [
        { ts: T2, price: 5 },
        { ts: T3, price: 6 },
      ],
      style: 'dashed',
      pattern: 'falling_wedge',
    }
    expect(dedupeTrendlines([a, b])).toEqual([a, b])
  })
})

describe('trendline grouping helpers (Plan 0067 phase 3)', () => {
  const line = (pattern: string | null, style: 'solid' | 'dashed'): TrendlineSpec => ({
    points: [
      { ts: T1, price: 10 },
      { ts: T2, price: 20 },
    ],
    style,
    pattern,
  })

  it('patternStateKey keys by pattern + style; unknown pattern folds to "unknown"', () => {
    expect(patternStateKey(line('rising_wedge', 'solid'))).toBe('rising_wedge|solid')
    expect(patternStateKey(line('rising_wedge', 'dashed'))).toBe('rising_wedge|dashed')
    expect(patternStateKey(line(null, 'solid'))).toBe('unknown|solid')
  })

  it('trendlineGroupLayerId namespaces the key under the trendline layer', () => {
    expect(trendlineGroupLayerId('rising_wedge|solid')).toBe('trendlines:rising_wedge|solid')
  })

  it('patternDisplayName maps known types and falls back to "Trendline"', () => {
    expect(patternDisplayName('head_shoulders')).toBe('Head & shoulders')
    expect(patternDisplayName('symmetrical_triangle')).toBe('Symmetrical triangle')
    expect(patternDisplayName('mystery')).toBe('Trendline')
    expect(patternDisplayName(null)).toBe('Trendline')
  })

  it('trendlineStateLabel reads solid as confirmed, dashed as forming', () => {
    expect(trendlineStateLabel('solid')).toBe('confirmed')
    expect(trendlineStateLabel('dashed')).toBe('forming')
  })
})

// Plan 0067 phase 2: the trendline hover hit test — pure point-to-segment
// distance + nearest-group selection, canvas-free.
const seg = (x1: number, y1: number, x2: number, y2: number): TrendlineSegment => ({
  x1,
  y1,
  x2,
  y2,
  color: '#000',
  dashed: false,
})

describe('pointSegmentDistance', () => {
  it('is zero for a point on the segment', () => {
    expect(pointSegmentDistance(5, 0, 0, 0, 10, 0)).toBe(0)
  })

  it('measures the perpendicular distance to the segment interior', () => {
    expect(pointSegmentDistance(5, 3, 0, 0, 10, 0)).toBeCloseTo(3)
  })

  it('measures to the nearer ENDPOINT when the projection falls off the end', () => {
    expect(pointSegmentDistance(-2, 0, 0, 0, 10, 0)).toBeCloseTo(2) // before start
    expect(pointSegmentDistance(13, 0, 0, 0, 10, 0)).toBeCloseTo(3) // past end
  })

  it('handles a degenerate zero-length segment as distance to its point', () => {
    expect(pointSegmentDistance(5, 8, 5, 5, 5, 5)).toBeCloseTo(3)
  })
})

describe('nearestTrendlineGroup', () => {
  const groups = [[seg(0, 0, 10, 0)], [seg(0, 20, 10, 20)]]

  it('returns the index of the group whose nearest segment is within tolerance', () => {
    expect(nearestTrendlineGroup(groups, 5, 1, 5)).toBe(0)
    expect(nearestTrendlineGroup(groups, 5, 19, 5)).toBe(1)
  })

  it('returns null when every group is farther than the tolerance', () => {
    expect(nearestTrendlineGroup(groups, 5, 10, 5)).toBeNull() // 10px from both
  })

  it('breaks a tie toward the LAST (topmost-drawn) group', () => {
    expect(nearestTrendlineGroup(groups, 5, 10, 10)).toBe(1) // equidistant, tol 10
  })
})

// Plan 0064 phase 1: the off-grid time→x fallback. `timeToCoordinate` is
// null-for-off-grid; these helpers map an anchor time onto the bar-grid logical
// scale (interpolating/extrapolating) so projected/extended lines still draw.
describe('timeToFractionalLogical', () => {
  const barTimes = [toUtc(T1), toUtc(T2), toUtc(T3)] // logical 0/1/2, 2-day spacing

  it('returns the integer logical for an exact bar time', () => {
    expect(timeToFractionalLogical(toUtc(T1), barTimes)).toBe(0)
    expect(timeToFractionalLogical(toUtc(T2), barTimes)).toBe(1)
    expect(timeToFractionalLogical(toUtc(T3), barTimes)).toBe(2)
  })

  it('interpolates a time between two bars', () => {
    // 2026-05-14 is one day into the two-day T1→T2 gap → half a logical unit.
    expect(timeToFractionalLogical(toUtc('2026-05-14T00:00:00Z'), barTimes)).toBeCloseTo(0.5)
  })

  it('extrapolates before the first bar (negative logical)', () => {
    expect(timeToFractionalLogical(toUtc('2026-05-11T00:00:00Z'), barTimes)).toBeCloseTo(-1)
  })

  it('extrapolates beyond the last bar', () => {
    expect(timeToFractionalLogical(toUtc('2026-05-19T00:00:00Z'), barTimes)).toBeCloseTo(3)
  })
})

describe('resolveTimeX', () => {
  const barTimes = [toUtc(T1), toUtc(T2), toUtc(T3)]
  const grid = (t: UTCTimestamp): number | null =>
    t === toUtc(T1) ? 100 : t === toUtc(T2) ? 160 : t === toUtc(T3) ? 220 : null
  const logical = (l: number): number => 100 + 60 * l

  it('uses the direct grid coordinate when the time is on a bar', () => {
    expect(resolveTimeX(toUtc(T2), grid, barTimes, logical)).toBe(160)
  })

  it('interpolates via the logical scale for an off-grid time', () => {
    expect(resolveTimeX(toUtc('2026-05-14T00:00:00Z'), grid, barTimes, logical)).toBeCloseTo(130)
  })

  it('extrapolates beyond the last bar', () => {
    expect(resolveTimeX(toUtc('2026-05-19T00:00:00Z'), grid, barTimes, logical)).toBeCloseTo(280)
  })

  it('returns null only on a degenerate grid (< 2 bars) — the sole surviving skip', () => {
    expect(resolveTimeX(toUtc('2026-05-14T00:00:00Z'), () => null, [toUtc(T1)], logical)).toBeNull()
  })
})

// A realistic 3-bar grid consistent with `timeToX`: bars at T1/T2/T3 sit at
// logical 0/1/2 and x 100/160/220, so `logicalToCoordinate(l) = 100 + 60*l`
// agrees with `timeToX` at the integer logicals and extrapolates off-grid.
const BAR_TIMES: UTCTimestamp[] = [toUtc(T1), toUtc(T2), toUtc(T3)]
const logicalToCoordinate = (l: number): number => 100 + 60 * l

/** Minimal `SeriesAttachedParameter` stub wiring the same converters in, plus
 * the bar grid (`series.data()`) and `logicalToCoordinate` the off-grid time
 * fallback reads. */
function attachStub(primitive: TrendlinePrimitive): void {
  const param = {
    chart: { timeScale: () => ({ timeToCoordinate: timeToX, logicalToCoordinate }) },
    series: { priceToCoordinate: priceToY, data: () => BAR_TIMES.map((t) => ({ time: t })) },
    requestUpdate: () => {},
  } as unknown as SeriesAttachedParameter<Time>
  primitive.attached(param)
}

describe('TrendlinePrimitive', () => {
  it('returns NO segments and NO pane view until attached (the spans precedent)', () => {
    const primitive = new TrendlinePrimitive(COLORS)
    primitive.setTrendlines([NECKLINE])
    expect(primitive.currentSegments()).toEqual([])
    // Pane view presence is governed by visibility + specs; segment math waits
    // for the chart/series (priceToCoordinate needs the candle series).
    expect(primitive.paneViews()).toHaveLength(1)
    attachStub(primitive)
    expect(primitive.currentSegments()).toHaveLength(1)
  })

  it('renders a forming hit dashed and a confirmed hit solid (style state)', () => {
    const primitive = new TrendlinePrimitive(COLORS)
    attachStub(primitive)
    primitive.setTrendlines([
      { ...NECKLINE, style: 'dashed', pattern: 'head_shoulders' }, // forming
      { ...NECKLINE, style: 'solid', pattern: 'double_top' }, // confirmed
    ])
    const segments = primitive.currentSegments()
    expect(segments.map((s) => s.dashed)).toEqual([true, false])
  })

  it('drops the pane view when hidden or empty, restores it when visible again', () => {
    const primitive = new TrendlinePrimitive(COLORS)
    attachStub(primitive)
    expect(primitive.paneViews()).toHaveLength(0) // empty → no view
    primitive.setTrendlines([NECKLINE])
    expect(primitive.paneViews()).toHaveLength(1)
    primitive.setVisible(false)
    expect(primitive.paneViews()).toHaveLength(0)
    primitive.setVisible(true)
    expect(primitive.paneViews()).toHaveLength(1)
  })

  it('draws an OFF-GRID / beyond-range trendline via the bar-grid logical fallback (Plan 0064 phase 1)', () => {
    // The live failure: a neckline whose endpoints are NOT exact bar times — one
    // between two loaded bars, one past the last loaded bar. `timeToCoordinate`
    // returns null for both, so pre-fix the whole segment was skipped (0
    // segments). The fallback resolves them off the bar-grid logical scale.
    const primitive = new TrendlinePrimitive(COLORS)
    attachStub(primitive)
    const offGrid: TrendlineSpec = {
      points: [
        { ts: '2026-05-14T00:00:00Z', price: 10 }, // between T1/T2 → logical 0.5 → x 130
        { ts: '2026-05-19T00:00:00Z', price: 20 }, // 2 days past T3 → logical 3 → x 280
      ],
      role: 'neckline',
      style: 'solid',
      pattern: 'head_shoulders',
    }
    primitive.setTrendlines([offGrid])
    const segments = primitive.currentSegments()
    expect(segments).toHaveLength(1)
    // priceToY: 120 - 4*price → price 10 = y 80, price 20 = y 40.
    expect(segments[0]).toMatchObject({ x1: 130, y1: 80, x2: 280, y2: 40 })
  })

  it('still draws an ON-GRID trendline unchanged (non-regression)', () => {
    const primitive = new TrendlinePrimitive(COLORS)
    attachStub(primitive)
    primitive.setTrendlines([NECKLINE]) // anchors on T1/T2 → direct grid path
    expect(primitive.currentSegments()).toEqual([
      expect.objectContaining({ x1: 100, y1: 80, x2: 160, y2: 40 }),
    ])
  })

  it('recolours in place via setColors (theme flip path)', () => {
    const primitive = new TrendlinePrimitive(COLORS)
    attachStub(primitive)
    primitive.setTrendlines([NECKLINE]) // pattern head_shoulders → its type hue
    expect(primitive.currentSegments()[0].color).toBe(COLORS.head_shoulders)
    primitive.setColors({ ...COLORS, head_shoulders: '#123456' })
    expect(primitive.currentSegments()[0].color).toBe('#123456')
  })

  // Plan 0067 phase 2: hover hit test. NECKLINE draws (100,80)-(160,40) under the
  // stub scales, so its midpoint is (130,60).
  it('hitTestTrendline returns the spec for a cursor near a line and null when far', () => {
    const primitive = new TrendlinePrimitive(COLORS)
    attachStub(primitive)
    primitive.setTrendlines([NECKLINE])
    expect(primitive.hitTestTrendline(130, 60)).toBe(NECKLINE) // on the line
    expect(primitive.hitTestTrendline(130, 200)).toBeNull() // far below
  })

  it('hitTestTrendline respects the pixel tolerance', () => {
    const primitive = new TrendlinePrimitive(COLORS)
    attachStub(primitive)
    // A horizontal line is easiest to reason about: (100,80)→(160,80) at y=80.
    const flat: TrendlineSpec = {
      points: [
        { ts: T1, price: 10 }, // priceToY 120-4*10 = 80
        { ts: T2, price: 10 },
      ],
      style: 'solid',
      pattern: 'double_top',
    }
    primitive.setTrendlines([flat])
    expect(primitive.hitTestTrendline(130, 80 + TRENDLINE_HIT_TOLERANCE_PX)).toBe(flat) // at tol
    expect(primitive.hitTestTrendline(130, 80 + TRENDLINE_HIT_TOLERANCE_PX + 1)).toBeNull() // past tol
  })

  it('hitTestTrendline returns null while hidden', () => {
    const primitive = new TrendlinePrimitive(COLORS)
    attachStub(primitive)
    primitive.setTrendlines([NECKLINE])
    primitive.setVisible(false)
    expect(primitive.hitTestTrendline(130, 60)).toBeNull()
  })

  it('hitTest (library hook) reports a hovered item near a line, null when far', () => {
    const primitive = new TrendlinePrimitive(COLORS)
    attachStub(primitive)
    primitive.setTrendlines([NECKLINE])
    const hit = primitive.hitTest(130, 60)
    expect(hit).toMatchObject({ externalId: 'trendlines:0', zOrder: 'top', cursorStyle: 'pointer' })
    expect(primitive.hitTest(130, 200)).toBeNull()
  })

  // Plan 0067 phase 3: hovering a legend group emphasises its lines, dims the rest.
  it('setHighlightedGroup emphasises the matching group and dims the rest', () => {
    const primitive = new TrendlinePrimitive(COLORS)
    attachStub(primitive)
    const hs: TrendlineSpec = { ...NECKLINE, pattern: 'head_shoulders', style: 'solid' }
    const dt: TrendlineSpec = { ...NECKLINE, pattern: 'double_top', style: 'solid' }
    primitive.setTrendlines([hs, dt])
    // No highlight → no emphasis/dim flags on any segment.
    expect(primitive.currentSegments().every((s) => !s.emphasis && !s.dimmed)).toBe(true)

    primitive.setHighlightedGroup('head_shoulders|solid')
    expect(primitive.highlightedGroup()).toBe('head_shoulders|solid')
    const segs = primitive.currentSegments()
    expect(segs[0]).toMatchObject({ emphasis: true }) // hs matches → emphasised
    expect(segs[1]).toMatchObject({ dimmed: true }) // dt → dimmed
    expect(segs[0].dimmed).toBeUndefined()

    primitive.setHighlightedGroup(null)
    expect(primitive.currentSegments().every((s) => !s.emphasis && !s.dimmed)).toBe(true)
  })
})
