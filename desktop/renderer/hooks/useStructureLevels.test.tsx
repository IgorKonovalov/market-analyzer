import { renderHook } from '@testing-library/react'
import type { RefObject } from 'react'
import type { IPriceLine } from 'lightweight-charts'

import { useStructureLevels } from './useStructureLevels'
import type { MainSeries } from '../lib/chartSeries'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'

function fakeSeries(): {
  series: MainSeries
  created: Array<{ price: number; title: string; color: string; line: IPriceLine }>
  removed: IPriceLine[]
} {
  const created: Array<{ price: number; title: string; color: string; line: IPriceLine }> = []
  const removed: IPriceLine[] = []
  const series = {
    createPriceLine: (opts: { price: number; title: string; color: string }) => {
      const line = { applyOptions: jest.fn() } as unknown as IPriceLine
      created.push({ price: opts.price, title: opts.title, color: opts.color, line })
      return line
    },
    removePriceLine: (line: IPriceLine) => removed.push(line),
  } as unknown as MainSeries
  return { series, created, removed }
}

function iso(i: number): string {
  return `2025-01-${String(i + 1).padStart(2, '0')}T00:00:00+00:00`
}

/** Flat band with a swing low (50 at bar 6) and swing high (150 at bar 14) — the
 * single dominant 50<->150 leg the fib grid auto-anchors to. */
function swingBars(): Bar[] {
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
    const mid = (high + low) / 2
    bars.push({
      symbol: 'T',
      timeframe: '1d',
      event_ts: iso(i),
      open: mid,
      high,
      low,
      close: mid,
      volume: 1000,
      source: 'test',
    })
  }
  return bars
}

const FIB: OverlaySpec = { kind: 'fibonacci' }
const PIVOT: OverlaySpec = { kind: 'pivot_points' }

describe('useStructureLevels', () => {
  it('draws the auto-anchored fibonacci grid at the right prices', () => {
    const { series, created } = fakeSeries()
    const ref: RefObject<Map<string, IPriceLine>> = { current: new Map() }
    renderHook(() =>
      useStructureLevels({ current: series }, ref, {
        bars: swingBars(),
        overlays: [FIB],
        hidden: new Set(),
        rebuildToken: 'candles',
      }),
    )
    // Five retracement lines + the two 0/1 anchor boundaries (Plan 0105 ph5),
    // labeled, at the hand-computed prices (high 150, low 50).
    expect(created).toHaveLength(7)
    const byTitle = Object.fromEntries(created.map((c) => [c.title, c.price]))
    expect(byTitle['Fib 0.5']).toBeCloseTo(100, 6)
    expect(byTitle['Fib 0.618']).toBeCloseTo(88.2, 6)
    expect(byTitle['Fib 0.786']).toBeCloseTo(71.4, 6)
    // The anchors sit at the swing endpoints and disclose the anchoring leg.
    expect(byTitle['Fib 0 — bullish leg high']).toBeCloseTo(150, 6)
    expect(byTitle['Fib 1 — bullish leg low']).toBeCloseTo(50, 6)
  })

  // Plan 0105 phase 5 (ADR-0100 rule 2): per-ratio colours, anchors neutral.
  it('draws each fib level in its own colour, distinct from the anchors', () => {
    const { series, created } = fakeSeries()
    const ref: RefObject<Map<string, IPriceLine>> = { current: new Map() }
    renderHook(() =>
      useStructureLevels({ current: series }, ref, {
        bars: swingBars(),
        overlays: [FIB],
        hidden: new Set(),
        rebuildToken: 'candles',
      }),
    )
    const levelColors = created.filter((c) => c.title.startsWith('Fib 0.')).map((c) => c.color)
    // Every level has its own hue — no two levels share a colour.
    expect(new Set(levelColors).size).toBe(levelColors.length)
    const anchorColors = created.filter((c) => c.title.includes('leg')).map((c) => c.color)
    expect(anchorColors).toHaveLength(2)
    // Both anchors share the neutral frame colour, unused by any level.
    expect(new Set(anchorColors).size).toBe(1)
    expect(levelColors).not.toContain(anchorColors[0])
  })

  it('draws seven classic pivot lines and ignores non-structure kinds', () => {
    const { series, created } = fakeSeries()
    const ref: RefObject<Map<string, IPriceLine>> = { current: new Map() }
    renderHook(() =>
      useStructureLevels({ current: series }, ref, {
        bars: swingBars(),
        overlays: [PIVOT, { kind: 'ema', period: 20 }],
        hidden: new Set(),
        rebuildToken: 'candles',
      }),
    )
    const titles = created.map((c) => c.title).sort()
    expect(titles).toEqual(['P', 'R1', 'R2', 'R3', 'S1', 'S2', 'S3'])
  })

  it('removes the grid when its legend row is toggled off, and re-creates on re-check', () => {
    const { series, created, removed } = fakeSeries()
    const ref: RefObject<Map<string, IPriceLine>> = { current: new Map() }
    const props = {
      bars: swingBars(),
      overlays: [FIB] as OverlaySpec[],
      hidden: new Set<string>(),
      rebuildToken: 'candles',
    }
    const { rerender } = renderHook(
      (p: typeof props) => useStructureLevels({ current: series }, ref, p),
      {
        initialProps: props,
      },
    )
    expect(created).toHaveLength(7)

    rerender({ ...props, hidden: new Set(['overlay:fibonacci:na']) })
    expect(removed).toHaveLength(7)
    expect(ref.current?.size).toBe(0)

    rerender({ ...props, hidden: new Set() })
    expect(created).toHaveLength(14)
    expect(ref.current?.size).toBe(7)
  })
})
