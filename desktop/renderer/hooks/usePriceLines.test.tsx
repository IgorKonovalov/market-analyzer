import { renderHook } from '@testing-library/react'
import type { RefObject } from 'react'
import type { IPriceLine } from 'lightweight-charts'

import { usePriceLines } from './usePriceLines'
import type { MainSeries } from '../lib/chartSeries'
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

const R1: OverlaySpec = {
  kind: 'price_line',
  price: 100,
  label: 'R1',
  role: 'resistance',
} as OverlaySpec
const EMA: OverlaySpec = { kind: 'ema', period: 20 } as OverlaySpec

describe('usePriceLines', () => {
  it('creates a price line for a price_line overlay and ignores non-price_line kinds', () => {
    const { series, created } = fakeSeries()
    const container = document.createElement('div')
    const priceLinesRef: RefObject<Map<string, IPriceLine>> = { current: new Map() }
    renderHook(() =>
      usePriceLines({ current: series }, { current: container }, priceLinesRef, {
        overlays: [R1, EMA], // EMA must be ignored (not a horizontal price line)
        hidden: new Set(),
        effectiveTheme: 'light',
        styleVersion: 0,
        rebuildToken: 'candles',
      }),
    )
    expect(created).toHaveLength(1)
    expect(created[0]).toMatchObject({ price: 100, title: 'R1' })
    expect(priceLinesRef.current?.size).toBe(1)
  })

  it('removes the line when its legend row is toggled off, and re-creates it when re-checked', () => {
    const { series, created, removed } = fakeSeries()
    const container = document.createElement('div')
    const priceLinesRef: RefObject<Map<string, IPriceLine>> = { current: new Map() }
    const props = {
      overlays: [R1] as OverlaySpec[],
      hidden: new Set<string>(),
      effectiveTheme: 'light' as const,
      styleVersion: 0,
      rebuildToken: 'candles',
    }
    const { rerender } = renderHook(
      (p: typeof props) =>
        usePriceLines({ current: series }, { current: container }, priceLinesRef, p),
      {
        initialProps: props,
      },
    )
    expect(created).toHaveLength(1)

    // Toggle R1's legend row off → the line is removed.
    rerender({ ...props, hidden: new Set(['pline:R1']) })
    expect(removed).toHaveLength(1)
    expect(priceLinesRef.current?.size).toBe(0)

    // Re-check → re-created.
    rerender({ ...props, hidden: new Set() })
    expect(created).toHaveLength(2)
    expect(priceLinesRef.current?.size).toBe(1)
  })
})
