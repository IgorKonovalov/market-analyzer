/**
 * Plan 0097 phase 2: the pure drawing geometry + the `DrawingPrimitive` hit-test
 * (`lib/drawings.ts`).
 *
 * `computeRayFarPoint` / `computeDrawingGeometry` map anchors to pixel segments
 * via stubbed converters (the `trendlines.test.ts` precedent), pinning the ray's
 * extend-to-edge behaviour. The primitive tests drive an attached fake chart so
 * `hitTestDrawingId` selects the segment and misses empty space, and
 * `hitTestHandle` grabs an endpoint — the edit engine's selection surface.
 */
import type { SeriesAttachedParameter, Time, UTCTimestamp } from 'lightweight-charts'

import type { DrawingSpec, TimePricePoint } from '../types/events'
import { DrawingPrimitive, computeDrawingGeometry, computeRayFarPoint } from './drawings'

const T1 = '2026-05-13T00:00:00Z'
const T2 = '2026-05-15T00:00:00Z'
const toUtc = (iso: string): UTCTimestamp =>
  Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp
const tp = (ts: string, price: number): TimePricePoint => ({ ts, price })

// time→x: T1→100, T2→200; else off-screen. price→y: y = 300 - price (linear).
const timeToX = (t: UTCTimestamp): number | null =>
  t === toUtc(T1) ? 100 : t === toUtc(T2) ? 200 : null
const priceToY = (price: number): number | null => (price < 0 ? null : 300 - price)

function spec(kind: DrawingSpec['kind'], id = 'd1'): DrawingSpec {
  return { kind, points: [tp(T1, 100), tp(T2, 120)], provenance: 'user', id }
}

describe('computeRayFarPoint', () => {
  it('extends a ray to the far chart edge in its direction', () => {
    // From (10,10) through (20,20): direction (1,1) hits the 100×100 corner.
    expect(computeRayFarPoint(10, 10, 20, 20, 100, 100)).toEqual({ x: 100, y: 100 })
  })

  it('extends horizontally to the right edge', () => {
    // From (10,50) through (30,50): horizontal → reaches x=width, y unchanged.
    expect(computeRayFarPoint(10, 50, 30, 50, 400, 300)).toEqual({ x: 400, y: 50 })
  })

  it('returns the second anchor for a degenerate zero-length ray', () => {
    expect(computeRayFarPoint(10, 10, 10, 10, 100, 100)).toEqual({ x: 10, y: 10 })
  })
})

describe('computeDrawingGeometry', () => {
  it('maps a trendline to a single segment between its two anchors', () => {
    const g = computeDrawingGeometry(spec('trendline'), timeToX, priceToY, 400, 300)
    expect(g).not.toBeNull()
    expect(g!.segments).toEqual([{ x1: 100, y1: 200, x2: 200, y2: 180 }])
    expect(g!.handles).toEqual([
      { x: 100, y: 200 },
      { x: 200, y: 180 },
    ])
  })

  it('extends a ray from anchor 0 through anchor 1 to the chart edge', () => {
    const g = computeDrawingGeometry(spec('ray'), timeToX, priceToY, 400, 300)
    expect(g).not.toBeNull()
    // Direction (100,-20) from (100,200): reaches x=400 first (t=3) → (400, 140).
    expect(g!.segments).toEqual([{ x1: 100, y1: 200, x2: 400, y2: 140 }])
    // Handles stay at the two DEFINING anchors, not the far point.
    expect(g!.handles).toEqual([
      { x: 100, y: 200 },
      { x: 200, y: 180 },
    ])
  })

  it('returns null when a defining anchor maps off-screen', () => {
    const offGrid: DrawingSpec = {
      ...spec('trendline'),
      points: [tp(T1, 100), tp('2026-06-01T00:00:00Z', 120)],
    }
    expect(computeDrawingGeometry(offGrid, timeToX, priceToY, 400, 300)).toBeNull()
  })

  it('honours the spec style colour/width and the agent default colour', () => {
    const styled: DrawingSpec = { ...spec('trendline'), style: { color: '#ff0000', width: 4 } }
    const g = computeDrawingGeometry(styled, timeToX, priceToY, 400, 300)
    expect(g!.color).toBe('#ff0000')
    expect(g!.width).toBe(4)
    // Unstyled → the supplied default colour (Plan 0097 phase 4 feeds the agent hue).
    const plain = computeDrawingGeometry(spec('trendline'), timeToX, priceToY, 400, 300, '#f08c00')
    expect(plain!.color).toBe('#f08c00')
  })
})

/** Attach the primitive to a fake chart whose time/price scales use our stubs, so
 * `renderGeometry` populates the hit-test cache without a real chart. */
function attach(primitive: DrawingPrimitive): void {
  const timeScale = {
    timeToCoordinate: (t: UTCTimestamp) => timeToX(t),
    logicalToCoordinate: () => null,
  }
  const chart = { timeScale: () => timeScale }
  const series = {
    data: () => [] as ReadonlyArray<{ time: unknown }>,
    priceToCoordinate: (p: number) => priceToY(p),
  }
  primitive.attached({
    chart,
    series,
    requestUpdate: () => {},
  } as unknown as SeriesAttachedParameter<Time>)
}

describe('DrawingPrimitive hit-testing', () => {
  it('selects the drawing whose segment is under the pointer and misses empty space', () => {
    const primitive = new DrawingPrimitive()
    attach(primitive)
    primitive.setDrawings([spec('trendline', 'line-1')])
    // Populate the cache the way a paint would (media size 400×300).
    primitive.renderGeometry(400, 300)

    // Midpoint of the segment (100,200)-(200,180) is (150,190).
    expect(primitive.hitTestDrawingId(150, 190)).toBe('line-1')
    // Well away from the line → no hit.
    expect(primitive.hitTestDrawingId(150, 260)).toBeNull()
  })

  it('grabs an endpoint handle within tolerance', () => {
    const primitive = new DrawingPrimitive()
    attach(primitive)
    primitive.setDrawings([spec('trendline', 'line-1')])
    primitive.renderGeometry(400, 300)

    // Handle 1 sits at (200,180); a pointer 2px away catches it.
    expect(primitive.hitTestHandle('line-1', 201, 181)).toBe(1)
    // Far from any handle → none.
    expect(primitive.hitTestHandle('line-1', 150, 190)).toBeNull()
  })

  it('reports no hit while hidden', () => {
    const primitive = new DrawingPrimitive()
    attach(primitive)
    primitive.setDrawings([spec('trendline', 'line-1')])
    primitive.renderGeometry(400, 300)
    primitive.setVisible(false)
    expect(primitive.hitTestDrawingId(150, 190)).toBeNull()
  })
})
