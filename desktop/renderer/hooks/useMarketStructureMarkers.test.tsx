/**
 * Plan 0105 phase 7 done-when (the headless-testable part): the hook publishes
 * the DRAWN structure markers time-keyed for the tooltip lookup — pivots and
 * events with their labels — and publishes NONE when the layer is toggled off
 * (mirroring the `drawnMarkers` gate, so a hidden layer shows no hover).
 */
import { renderHook } from '@testing-library/react'
import type { RefObject } from 'react'

import { useMarketStructureMarkers } from './useMarketStructureMarkers'
import { MARKET_STRUCTURE_LAYER_ID } from '../lib/chartSeries'
import type { MainSeries } from '../lib/chartSeries'
import type { MarketStructureResult } from '../lib/marketStructure'
import type { Bar } from '../types/sidecar/bar'

function iso(i: number): string {
  return `2026-04-${String(i + 1).padStart(2, '0')}T00:00:00+00:00`
}

function utc(i: number): number {
  return Math.floor(new Date(iso(i)).getTime() / 1000)
}

const BARS: Bar[] = Array.from({ length: 10 }, (_, i) => ({
  symbol: 'T',
  timeframe: '1d',
  event_ts: iso(i),
  open: 100,
  high: 102,
  low: 99,
  close: 101,
  volume: 1000,
  source: 'test',
}))

const STRUCTURE: MarketStructureResult = {
  structuralTrend: 'up',
  labeledPivots: [
    { pivot: { barIndex: 2, ts: iso(2), price: 102, kind: 'high' }, label: 'HH' },
    { pivot: { barIndex: 5, ts: iso(5), price: 99, kind: 'low' }, label: 'HL' },
  ],
  events: [{ kind: 'BOS', direction: 'bullish', barIndex: 7, price: 102 }],
}

const seriesRef: RefObject<MainSeries | null> = {
  current: { setData: jest.fn(), applyOptions: jest.fn() } as unknown as MainSeries,
}
const containerRef = { current: document.createElement('div') } as RefObject<HTMLDivElement>

function renderStructure(hidden: ReadonlySet<string>) {
  return renderHook(() =>
    useMarketStructureMarkers(seriesRef, containerRef, {
      structure: STRUCTURE,
      bars: BARS,
      hidden,
      effectiveTheme: 'light',
      styleVersion: 0,
      rebuildToken: 'candles',
    }),
  )
}

describe('useMarketStructureMarkers — drawn points for the tooltip (Plan 0105 ph7)', () => {
  it('publishes the drawn pivots + events, time-keyed and labeled', () => {
    const { result } = renderStructure(new Set())
    expect(result.current).toEqual([
      { time: utc(2), label: 'HH' },
      { time: utc(5), label: 'HL' },
      { time: utc(7), label: 'BOS' },
    ])
  })

  it('publishes nothing when the structure layer is toggled off', () => {
    const { result } = renderStructure(new Set([MARKET_STRUCTURE_LAYER_ID]))
    expect(result.current).toEqual([])
  })
})
