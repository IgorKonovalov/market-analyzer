/**
 * Plan 0052 phase 4: the pure trendline helpers + primitive in
 * `lib/trendlines.ts`.
 *
 * `computeTrendlineSegments` maps anchor pairs to pixel segments via stubbed
 * time→x / price→y converters, skipping a segment whose either endpoint maps
 * off-screen and colouring by role token — canvas-free, the `spans.test.ts`
 * precedent. The primitive tests pin the forming/confirmed styling (dashed vs
 * solid on its segment state) and the empty-until-attached / visibility
 * behaviour.
 */
import type { SeriesAttachedParameter, Time, UTCTimestamp } from 'lightweight-charts'

import type { TrendlineSpec } from '../types/events'
import {
  TrendlinePrimitive,
  computeTrendlineSegments,
  resolveTimeX,
  timeToFractionalLogical,
  trendlineColor,
  type TrendlineColors,
} from './trendlines'

const COLORS: TrendlineColors = {
  bullish: '#00ff00',
  bearish: '#ff0000',
  neutral: '#888888',
  accent: '#0000ff',
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

  it('colours by role token: neckline=accent, upper=bearish, lower=bullish, none=neutral', () => {
    expect(trendlineColor('neckline', COLORS)).toBe(COLORS.accent)
    expect(trendlineColor('upper_trendline', COLORS)).toBe(COLORS.bearish)
    expect(trendlineColor('lower_trendline', COLORS)).toBe(COLORS.bullish)
    expect(trendlineColor(null, COLORS)).toBe(COLORS.neutral)
    expect(trendlineColor(undefined, COLORS)).toBe(COLORS.neutral)

    const segments = computeTrendlineSegments(
      [{ ...NECKLINE, role: 'lower_trendline' }],
      timeToX,
      priceToY,
      COLORS,
    )
    expect(segments[0].color).toBe(COLORS.bullish)
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
    primitive.setTrendlines([NECKLINE])
    expect(primitive.currentSegments()[0].color).toBe(COLORS.accent)
    primitive.setColors({ ...COLORS, accent: '#123456' })
    expect(primitive.currentSegments()[0].color).toBe('#123456')
  })
})
