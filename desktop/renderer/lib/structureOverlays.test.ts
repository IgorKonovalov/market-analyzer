/**
 * Plan 0092 phase 5: the client-side price-structure compute mirrors match the
 * Python reference (`analysis/fibonacci.py`, `levels.py::pivot_points`,
 * `volume.py::anchored_vwap`, `levels.py::swing_pivots`) — the same hand-computed
 * grids the sidecar tests pin, so the client draw lands at the same prices the
 * agent-drawn overlay would.
 */
import { anchoredVwapSeries, resolveAnchorIndex } from './anchoredVwap'
import { fibAnchorLines, fibonacciGrid } from './fibonacci'
import { pivotPoints } from './pivots'
import { dominantSwing, swingPivots } from './swings'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'

const TOL = 1e-9

function iso(i: number): string {
  return `2025-01-${String(i + 1).padStart(2, '0')}T00:00:00+00:00`
}

function mkBar(i: number, high: number, low: number, close: number, volume = 1000): Bar {
  const mid = (high + low) / 2
  return {
    symbol: 'T',
    timeframe: '1d',
    event_ts: iso(i),
    open: mid,
    high,
    low,
    close,
    volume,
    source: 'test',
  }
}

/** Flat 99..101 band with one swing low (50 at bar 6) and one swing high (150 at
 * bar 14) — the single dominant 50<->150 leg (mirrors the Python test_fibonacci). */
function swingFixture(): Bar[] {
  const bars: Bar[] = []
  for (let i = 0; i < 30; i++) {
    let high = 101
    let low = 99
    if (i === 6) {
      high = 91
      low = 50
    }
    if (i === 14) {
      high = 150
      low = 109
    }
    bars.push(mkBar(i, high, low, (high + low) / 2))
  }
  return bars
}

describe('swingPivots + dominantSwing', () => {
  it('finds the constructed high and low, and the dominant leg', () => {
    const bars = swingFixture()
    const pivots = swingPivots(bars)
    const highs = pivots.filter((p) => p.kind === 'high')
    const lows = pivots.filter((p) => p.kind === 'low')
    expect(lows.some((p) => Math.abs(p.price - 50) < TOL)).toBe(true)
    expect(highs.some((p) => Math.abs(p.price - 150) < TOL)).toBe(true)

    const swing = dominantSwing(bars)
    expect(swing).not.toBeNull()
    expect(swing!.high.price).toBeCloseTo(150, 9)
    expect(swing!.low.price).toBeCloseTo(50, 9)
    // low (bar 6) printed before high (bar 14).
    expect(swing!.low.ts < swing!.high.ts).toBe(true)
  })

  it('is trailing — an unconfirmed swing is not picked early', () => {
    const bars = swingFixture()
    // The high at bar 14 confirms only once its 3 right bars exist (bar 17).
    expect(dominantSwing(bars.slice(0, 16))).toBeNull()
  })
})

describe('fibonacciGrid', () => {
  it('auto-anchored retracement matches the hand grid (bullish)', () => {
    const grid = fibonacciGrid(swingFixture(), { kind: 'fibonacci' })
    expect(grid).not.toBeNull()
    expect(grid!.kind).toBe('retracement')
    expect(grid!.direction).toBe('bullish')
    const byRatio = Object.fromEntries(grid!.levels.map((l) => [l.ratio, l.price]))
    expect(byRatio['0.236']).toBeCloseTo(126.4, 9)
    expect(byRatio['0.382']).toBeCloseTo(111.8, 9)
    expect(byRatio['0.5']).toBeCloseTo(100.0, 9)
    expect(byRatio['0.618']).toBeCloseTo(88.2, 9)
    expect(byRatio['0.786']).toBeCloseTo(71.4, 9)
  })

  it('auto-anchored extension projects off the last close', () => {
    const grid = fibonacciGrid(swingFixture(), { kind: 'fibonacci', fib_kind: 'extension' })
    expect(grid).not.toBeNull()
    expect(grid!.kind).toBe('extension')
    const byRatio = Object.fromEntries(grid!.levels.map((l) => [l.ratio, l.price]))
    // base = last close = 100, span = 100, bullish.
    expect(byRatio['1.272']).toBeCloseTo(227.2, 9)
    expect(byRatio['2.0']).toBeCloseTo(300.0, 9) // key is "2.0", not "2"
  })

  it('honours an explicit swing anchor (bearish when high printed first)', () => {
    const spec: OverlaySpec = {
      kind: 'fibonacci',
      high_anchor_ts: iso(1),
      high_anchor_price: 150,
      low_anchor_ts: iso(9),
      low_anchor_price: 50,
    }
    const grid = fibonacciGrid(swingFixture(), spec)
    expect(grid!.direction).toBe('bearish')
    const byRatio = Object.fromEntries(grid!.levels.map((l) => [l.ratio, l.price]))
    expect(byRatio['0.382']).toBeCloseTo(88.2, 9) // low + r*span
    expect(byRatio['0.618']).toBeCloseTo(111.8, 9)
  })

  it('returns null when there is no dominant swing (flat series)', () => {
    const flat = Array.from({ length: 20 }, (_, i) => mkBar(i, 101, 99, 100))
    expect(fibonacciGrid(flat, { kind: 'fibonacci' })).toBeNull()
  })

  // Plan 0105 phase 5: the grid exposes its resolved anchors (display-only —
  // the level-price pins above are untouched by the addition).
  it('exposes the resolved swing anchors on the grid', () => {
    const grid = fibonacciGrid(swingFixture(), { kind: 'fibonacci' })!
    expect(grid.anchors.highPrice).toBeCloseTo(150, 9)
    expect(grid.anchors.lowPrice).toBeCloseTo(50, 9)
    expect(grid.anchors.highTs).toBe(iso(14))
    expect(grid.anchors.lowTs).toBe(iso(6))
  })

  it('exposes an explicit anchor verbatim', () => {
    const grid = fibonacciGrid(swingFixture(), {
      kind: 'fibonacci',
      high_anchor_ts: iso(1),
      high_anchor_price: 150,
      low_anchor_ts: iso(9),
      low_anchor_price: 50,
    })!
    expect(grid.anchors).toEqual({
      highTs: iso(1),
      highPrice: 150,
      lowTs: iso(9),
      lowPrice: 50,
    })
  })
})

describe('fibAnchorLines', () => {
  it('titles the 0/1 endpoints for a bullish retracement (0 at the leg high)', () => {
    const grid = fibonacciGrid(swingFixture(), { kind: 'fibonacci' })!
    expect(fibAnchorLines(grid)).toEqual([
      { key: 'anchor0', price: 150, title: 'Fib 0 — bullish leg high' },
      { key: 'anchor1', price: 50, title: 'Fib 1 — bullish leg low' },
    ])
  })

  it('flips the endpoints for a bearish leg (0 at the leg low)', () => {
    const grid = fibonacciGrid(swingFixture(), {
      kind: 'fibonacci',
      high_anchor_ts: iso(1),
      high_anchor_price: 150,
      low_anchor_ts: iso(9),
      low_anchor_price: 50,
    })!
    expect(grid.direction).toBe('bearish')
    expect(fibAnchorLines(grid)).toEqual([
      { key: 'anchor0', price: 50, title: 'Fib 0 — bearish leg low' },
      { key: 'anchor1', price: 150, title: 'Fib 1 — bearish leg high' },
    ])
  })

  it('titles an extension grid as the source swing, not 0/1', () => {
    const grid = fibonacciGrid(swingFixture(), { kind: 'fibonacci', fib_kind: 'extension' })!
    const titles = fibAnchorLines(grid).map((a) => a.title)
    expect(titles).toEqual(['Fib anchor — bullish leg high', 'Fib anchor — bullish leg low'])
  })
})

describe('pivotPoints', () => {
  // Last bar H=140, L=90, C=100 — the Python test_pivots fixture.
  const bars = [mkBar(0, 200, 10, 50), mkBar(1, 140, 90, 100)]

  it('floor matches the hand grid', () => {
    const pp = pivotPoints(bars, 'floor')!
    expect(pp.pivot).toBeCloseTo(110, 9)
    expect(pp.resistances[0]).toBeCloseTo(130, 9)
    expect(pp.resistances[1]).toBeCloseTo(160, 9)
    expect(pp.resistances[2]).toBeCloseTo(180, 9)
    expect(pp.supports).toEqual([
      expect.closeTo(80, 9),
      expect.closeTo(60, 9),
      expect.closeTo(30, 9),
    ])
  })

  it('woodie matches the hand grid', () => {
    const pp = pivotPoints(bars, 'woodie')!
    expect(pp.pivot).toBeCloseTo(107.5, 9)
    expect(pp.resistances[1]).toBeCloseTo(157.5, 9)
    expect(pp.supports[1]).toBeCloseTo(57.5, 9)
  })

  it('camarilla matches the hand grid', () => {
    const pp = pivotPoints(bars, 'camarilla')!
    expect(pp.pivot).toBeCloseTo(110, 9)
    expect(pp.resistances[0]).toBeCloseTo(100 + 55 / 12, 9)
    expect(pp.supports[2]).toBeCloseTo(100 - 55 / 4, 9)
  })

  it('returns null for empty bars', () => {
    expect(pivotPoints([], 'floor')).toBeNull()
  })
})

describe('anchoredVwapSeries', () => {
  function flat(i: number, tp: number, volume: number): Bar {
    return mkBar(i, tp, tp, tp, volume)
  }
  const bars = [flat(0, 10, 100), flat(1, 20, 100), flat(2, 30, 200), flat(3, 40, 100)]

  it('accumulates from an explicit anchor', () => {
    const series = anchoredVwapSeries(bars, iso(1))
    expect(series).toHaveLength(3) // from bar 1 to bar 3
    expect(series[0].value).toBeCloseTo(20, 9)
    expect(series[1].value).toBeCloseTo(8000 / 300, 9)
    expect(series[2].value).toBeCloseTo(30, 9)
  })

  it('auto-anchors to the dominant-swing start when no anchor is given', () => {
    const idx = resolveAnchorIndex(swingFixture())
    // The dominant swing's earlier pivot is the low at bar 6.
    expect(idx).toBe(6)
  })

  it('skips a leading zero-volume run (no divide-by-zero)', () => {
    const zeros = [flat(0, 10, 0), flat(1, 20, 0), flat(2, 30, 50)]
    const series = anchoredVwapSeries(zeros, iso(0))
    expect(series).toHaveLength(1) // only bar 2 has accumulated volume
    expect(series[0].value).toBeCloseTo(30, 9)
  })
})
