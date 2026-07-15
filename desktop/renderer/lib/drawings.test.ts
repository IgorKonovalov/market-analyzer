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
import { FIB_ANCHOR_COLOR, fibLevelColor } from './overlays'

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

describe('computeDrawingGeometry — phase-3 kinds', () => {
  const oneAnchor = (kind: 'hline' | 'vline', ts: string, price: number): DrawingSpec => ({
    kind,
    points: [tp(ts, price)],
    provenance: 'user',
    id: 'k1',
  })

  it('hline spans the full width at the price y, handle at the anchor', () => {
    const g = computeDrawingGeometry(oneAnchor('hline', T1, 100), timeToX, priceToY, 400, 300)
    expect(g!.segments).toEqual([{ x1: 0, y1: 200, x2: 400, y2: 200 }])
    expect(g!.handles).toEqual([{ x: 100, y: 200 }])
  })

  it('hline still renders when its anchor ts is off-grid (no logical mis-render)', () => {
    // ts maps to null → the line still spans the axis; the handle falls into view.
    const g = computeDrawingGeometry(
      oneAnchor('hline', '2026-06-01T00:00:00Z', 100),
      timeToX,
      priceToY,
      400,
      300,
    )
    expect(g).not.toBeNull()
    expect(g!.segments).toEqual([{ x1: 0, y1: 200, x2: 400, y2: 200 }])
    expect(g!.handles[0].y).toBe(200)
    // Handle x clamped into the visible pane, not dropped.
    expect(g!.handles[0].x).toBeGreaterThan(0)
    expect(g!.handles[0].x).toBeLessThan(400)
  })

  it('vline spans the full height at the anchor x', () => {
    const g = computeDrawingGeometry(oneAnchor('vline', T1, 100), timeToX, priceToY, 400, 300)
    expect(g!.segments).toEqual([{ x1: 100, y1: 0, x2: 100, y2: 300 }])
    expect(g!.handles).toEqual([{ x: 100, y: 200 }])
  })

  it('rect renders four edges and a fill polygon of the four corners', () => {
    const g = computeDrawingGeometry(spec('rect'), timeToX, priceToY, 400, 300)
    // Corners a=(100,200) b=(200,180): the four edges of the box.
    expect(g!.segments).toEqual([
      { x1: 100, y1: 200, x2: 200, y2: 200 },
      { x1: 200, y1: 200, x2: 200, y2: 180 },
      { x1: 200, y1: 180, x2: 100, y2: 180 },
      { x1: 100, y1: 180, x2: 100, y2: 200 },
    ])
    expect(g!.fillPolygon).toHaveLength(4)
    expect(g!.handles).toEqual([
      { x: 100, y: 200 },
      { x: 200, y: 180 },
    ])
  })

  it('fib renders the six standard ratio lines, each in its palette colour', () => {
    const g = computeDrawingGeometry(spec('fib'), timeToX, priceToY, 400, 300)
    expect(g!.segments).toHaveLength(6)
    // a.y=200 (0%), b.y=180 (100%); lines span from the left anchor x to the edge.
    // The 0 / 100% endpoints are the swing-anchor boundaries → neutral slate.
    expect(g!.segments[0]).toEqual({
      x1: 100,
      y1: 200,
      x2: 400,
      y2: 200,
      label: '0.0%',
      color: FIB_ANCHOR_COLOR,
    })
    expect(g!.segments[5]).toEqual({
      x1: 100,
      y1: 180,
      x2: 400,
      y2: 180,
      label: '100.0%',
      color: FIB_ANCHOR_COLOR,
    })
    // 50% line sits midway (y=190), captioned, in its own golden-pocket hue.
    const mid = g!.segments.find((s) => s.label === '50.0%')
    expect(mid).toEqual({
      x1: 100,
      y1: 190,
      x2: 400,
      y2: 190,
      label: '50.0%',
      color: fibLevelColor('0.5'),
    })
  })

  it('fib colours the six levels distinctly (0/100 slate, interior graded)', () => {
    const g = computeDrawingGeometry(spec('fib'), timeToX, priceToY, 400, 300)
    const colors = g!.segments.map((s) => s.color)
    // 0 and 100% share the neutral anchor slate; the four interior ratios each
    // differ from it and from each other → a legible grid, not one colour.
    expect(colors[0]).toBe(FIB_ANCHOR_COLOR)
    expect(colors[5]).toBe(FIB_ANCHOR_COLOR)
    const interior = colors.slice(1, 5)
    expect(new Set(interior).size).toBe(4)
    for (const c of interior) expect(c).not.toBe(FIB_ANCHOR_COLOR)
    // The 50 / 61.8 golden pocket lands on the shared palette's watched pair.
    expect(colors[3]).toBe(fibLevelColor('0.5'))
    expect(colors[4]).toBe(fibLevelColor('0.618'))
  })

  it('a user style.color collapses the fib grid back to one colour', () => {
    const styled: DrawingSpec = { ...spec('fib'), style: { color: '#ff0000' } }
    const g = computeDrawingGeometry(styled, timeToX, priceToY, 400, 300)
    // Per-level palette is the unstyled default; an explicit colour wins, so no
    // segment carries its own colour — all fall back to the drawing's g.color.
    for (const s of g!.segments) expect(s.color).toBeUndefined()
    expect(g!.color).toBe('#ff0000')
  })
})

describe('computeDrawingGeometry — Plan 0104 position & range kinds', () => {
  const longSpec: DrawingSpec = {
    kind: 'long_position',
    points: [tp(T1, 100)],
    stop: 90,
    target: 120,
    provenance: 'user',
    id: 'pos-1',
  }

  it('long_position: entry/stop/target handles, red+green legs, R:R caption + two fill zones', () => {
    const g = computeDrawingGeometry(longSpec, timeToX, priceToY, 400, 300)
    expect(g).not.toBeNull()
    // entryY=200, stopY=210, targetY=180; entryX=100, extends to right=400.
    expect(g!.handles).toEqual([
      { x: 100, y: 200 }, // entry
      { x: 100, y: 210 }, // stop
      { x: 100, y: 180 }, // target
    ])
    // Entry line carries the derived R:R (|120-100|/|100-90| = 2.00).
    expect(g!.segments[0]).toEqual({ x1: 100, y1: 200, x2: 400, y2: 200, label: 'R:R 2.00' })
    expect(g!.segments[1].color).toBe('#e03131') // stop leg red
    expect(g!.segments[2].color).toBe('#2f9e44') // target leg green
    // Two coloured fill zones (stop leg red, target leg green).
    expect(g!.fills).toHaveLength(2)
    expect(g!.fills![0].color).toBe('#e03131')
    expect(g!.fills![1].color).toBe('#2f9e44')
  })

  it('short_position: stop above entry, target below, R:R still derived', () => {
    const shortSpec: DrawingSpec = {
      kind: 'short_position',
      points: [tp(T1, 100)],
      stop: 110,
      target: 80,
      provenance: 'user',
      id: 'pos-2',
    }
    const g = computeDrawingGeometry(shortSpec, timeToX, priceToY, 400, 300)
    // stopY=190 (above entryY=200 in pixels), targetY=220 (below). |80-100|/|100-110| = 2.
    expect(g!.handles).toEqual([
      { x: 100, y: 200 },
      { x: 100, y: 190 },
      { x: 100, y: 220 },
    ])
    expect(g!.segments[0].label).toBe('R:R 2.00')
  })

  it('date_range: two vertical lines + a bars·span readout on the connector', () => {
    const spec2: DrawingSpec = {
      kind: 'date_range',
      points: [tp(T1, 100), tp(T2, 120)],
      provenance: 'user',
      id: 'dr-1',
    }
    // Two bars in [T1, T2]; T1→T2 is 2 calendar days.
    const barTimes = [toUtc(T1), toUtc(T2)]
    const g = computeDrawingGeometry(spec2, timeToX, priceToY, 400, 300, undefined, barTimes)
    expect(g!.segments[0]).toEqual({ x1: 100, y1: 0, x2: 100, y2: 300 }) // vertical at xa
    expect(g!.segments[1]).toEqual({ x1: 200, y1: 0, x2: 200, y2: 300 }) // vertical at xb
    expect(g!.segments[2].label).toBe('2 bars · 2 days')
    expect(g!.handles).toEqual([
      { x: 100, y: 200 },
      { x: 200, y: 180 },
    ])
  })

  it('price_range: two horizontal lines + a Δprice/% readout', () => {
    const spec2: DrawingSpec = {
      kind: 'price_range',
      points: [tp(T1, 100), tp(T2, 120)],
      provenance: 'user',
      id: 'pr-1',
    }
    const g = computeDrawingGeometry(spec2, timeToX, priceToY, 400, 300)
    expect(g!.segments[0]).toEqual({ x1: 0, y1: 200, x2: 400, y2: 200 }) // horizontal at ya
    expect(g!.segments[1]).toEqual({ x1: 0, y1: 180, x2: 400, y2: 180 }) // horizontal at yb
    // Δ = 120-100 = 20, +20%.
    expect(g!.segments[2].label).toBe('Δ 20 (+20%)')
  })

  it('date_price_range: a boxed measure captioned with both readouts', () => {
    const spec2: DrawingSpec = {
      kind: 'date_price_range',
      points: [tp(T1, 100), tp(T2, 120)],
      provenance: 'user',
      id: 'dpr-1',
    }
    const barTimes = [toUtc(T1), toUtc(T2)]
    const g = computeDrawingGeometry(spec2, timeToX, priceToY, 400, 300, undefined, barTimes)
    expect(g!.fillPolygon).toHaveLength(4)
    expect(g!.segments[0].label).toBe('2 bars · 2 days · Δ 20 (+20%)')
  })

  it('long_position returns null when a level maps off-screen', () => {
    const offScreen: DrawingSpec = { ...longSpec, stop: -5 } // priceToY(<0) → null
    expect(computeDrawingGeometry(offScreen, timeToX, priceToY, 400, 300)).toBeNull()
  })

  it('an agent-placed position carries the advisory label on its entry leg (ADR-0029)', () => {
    const agentPos: DrawingSpec = { ...longSpec, provenance: 'agent', id: 'adv' }
    const g = computeDrawingGeometry(agentPos, timeToX, priceToY, 400, 300)
    expect(g!.segments[0].label).toBe('Advisory · R:R 2.00')
    // A user position shows the bare R:R — no advisory prefix.
    const userG = computeDrawingGeometry(longSpec, timeToX, priceToY, 400, 300)
    expect(userG!.segments[0].label).toBe('R:R 2.00')
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

  it('selects a rect by clicking near any of its edges', () => {
    const primitive = new DrawingPrimitive()
    attach(primitive)
    primitive.setDrawings([spec('rect', 'box-1')])
    primitive.renderGeometry(400, 300)
    // Near the top edge y=200 between x=100..200.
    expect(primitive.hitTestDrawingId(150, 201)).toBe('box-1')
    // Inside the box but away from every edge → no hit (edges, not fill, select).
    expect(primitive.hitTestDrawingId(150, 190)).toBeNull()
  })

  it('selects a fib grid by clicking near one of its ratio lines', () => {
    const primitive = new DrawingPrimitive()
    attach(primitive)
    primitive.setDrawings([spec('fib', 'fib-1')])
    primitive.renderGeometry(400, 300)
    // Near the 50% line (y=190).
    expect(primitive.hitTestDrawingId(250, 191)).toBe('fib-1')
    // Between ratio lines → no hit.
    expect(primitive.hitTestDrawingId(250, 250)).toBeNull()
  })

  it('selects a position box by a leg and grabs its three price handles (Plan 0104)', () => {
    const primitive = new DrawingPrimitive()
    attach(primitive)
    primitive.setDrawings([
      {
        kind: 'long_position',
        points: [tp(T1, 100)],
        stop: 90,
        target: 120,
        provenance: 'user',
        id: 'pos-1',
      },
    ])
    primitive.renderGeometry(400, 300)
    // Near the entry leg (y=200, spanning x=100..400).
    expect(primitive.hitTestDrawingId(250, 201)).toBe('pos-1')
    // Away from every leg (between target y=180 and entry y=200 interior).
    expect(primitive.hitTestDrawingId(250, 190)).toBeNull()
    // The three handles sit at the entry x (100): entry (0), stop (1), target (2).
    expect(primitive.hitTestHandle('pos-1', 100, 200)).toBe(0)
    expect(primitive.hitTestHandle('pos-1', 100, 210)).toBe(1)
    expect(primitive.hitTestHandle('pos-1', 100, 180)).toBe(2)
  })
})
