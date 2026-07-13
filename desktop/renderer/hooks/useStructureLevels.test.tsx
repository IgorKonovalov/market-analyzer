import { renderHook } from '@testing-library/react'
import type { RefObject } from 'react'
import type { IPriceLine } from 'lightweight-charts'

import { useStructureLevels } from './useStructureLevels'
import type { MainSeries } from '../lib/chartSeries'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'

function fakeSeries(): {
  series: MainSeries
  created: Array<{ price: number; title: string; line: IPriceLine }>
  removed: IPriceLine[]
} {
  const created: Array<{ price: number; title: string; line: IPriceLine }> = []
  const removed: IPriceLine[] = []
  const series = {
    createPriceLine: (opts: { price: number; title: string }) => {
      const line = { applyOptions: jest.fn() } as unknown as IPriceLine
      created.push({ price: opts.price, title: opts.title, line })
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
    // Five retracement lines, labeled, at the hand-computed prices (high 150, low 50).
    expect(created).toHaveLength(5)
    const byTitle = Object.fromEntries(created.map((c) => [c.title, c.price]))
    expect(byTitle['Fib 0.5']).toBeCloseTo(100, 6)
    expect(byTitle['Fib 0.618']).toBeCloseTo(88.2, 6)
    expect(byTitle['Fib 0.786']).toBeCloseTo(71.4, 6)
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
    expect(created).toHaveLength(5)

    rerender({ ...props, hidden: new Set(['overlay:fibonacci:na']) })
    expect(removed).toHaveLength(5)
    expect(ref.current?.size).toBe(0)

    rerender({ ...props, hidden: new Set() })
    expect(created).toHaveLength(10)
    expect(ref.current?.size).toBe(5)
  })
})
