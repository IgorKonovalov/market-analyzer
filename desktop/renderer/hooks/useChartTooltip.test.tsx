import { act, renderHook } from '@testing-library/react'
import type { IChartApi, MouseEventParams } from 'lightweight-charts'

import { useChartTooltip } from './useChartTooltip'
import type { OverlayEntry } from '../lib/chartSeries'
import type { ChartMarker } from '../lib/markers'
import type { Divergence } from '../types/events'
import { PatternSpanPrimitive } from '../lib/spans'
import { TrendlinePrimitive } from '../lib/trendlines'
import { DivergencePrimitive } from '../lib/divergences'

function harness(hitDivergence: DivergencePrimitive['hitTestDivergence'] = () => null) {
  let handler: ((p: MouseEventParams) => void) | null = null
  const chart = {
    subscribeCrosshairMove: (h: (p: MouseEventParams) => void) => {
      handler = h
    },
    unsubscribeCrosshairMove: jest.fn(),
  } as unknown as IChartApi
  const setHighlight = jest.fn()
  const span = { setHighlight } as unknown as PatternSpanPrimitive
  const trend = { hitTestTrendline: () => null } as unknown as TrendlinePrimitive
  const div = { hitTestDivergence: hitDivergence } as unknown as DivergencePrimitive
  const overlayRef = { current: new Map<string, OverlayEntry>() }
  return {
    chartRef: { current: chart },
    overlayRef,
    spanRef: { current: span },
    trendRef: { current: trend },
    divRef: { current: div },
    fire: (p: MouseEventParams) => act(() => handler?.(p)),
    setHighlight,
    unsub: chart.unsubscribeCrosshairMove as jest.Mock,
  }
}

const HAMMER: ChartMarker = {
  event_ts: '2026-04-10T00:00:00+00:00',
  kind: 'bullish_marker',
  pattern: 'hammer',
}
const HAMMER_TIME = Math.floor(Date.parse('2026-04-10T00:00:00+00:00') / 1000)

describe('useChartTooltip', () => {
  it('reports the hovered marker pattern name and clears on pointer-leave', () => {
    const h = harness()
    const { result } = renderHook(() =>
      useChartTooltip(h.chartRef, h.overlayRef, h.spanRef, h.trendRef, h.divRef, {
        drawnMarkers: [HAMMER],
        rebuildToken: 'candles',
      }),
    )
    expect(result.current).toBeNull()

    h.fire({
      point: { x: 40, y: 20 },
      time: HAMMER_TIME,
      seriesData: new Map(),
    } as unknown as MouseEventParams)
    expect(result.current?.content.markers).toContain('Hammer')
    expect(result.current).toMatchObject({ x: 40, y: 20 })

    // Pointer leaves the pane (no point) → tooltip cleared, highlight cleared.
    h.fire({ point: undefined } as unknown as MouseEventParams)
    expect(result.current).toBeNull()
    expect(h.setHighlight).toHaveBeenLastCalledWith(null)
  })

  it('unsubscribes the crosshair handler on unmount', () => {
    const h = harness()
    const { unmount } = renderHook(() =>
      useChartTooltip(h.chartRef, h.overlayRef, h.spanRef, h.trendRef, h.divRef, {
        drawnMarkers: [],
        rebuildToken: 'candles',
      }),
    )
    unmount()
    expect(h.unsub).toHaveBeenCalled()
  })

  it('reports the hovered divergence with its glossary meaning (Plan 0091 phase 9)', () => {
    const divergence: Divergence = {
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
    }
    const h = harness(() => divergence)
    const { result } = renderHook(() =>
      useChartTooltip(h.chartRef, h.overlayRef, h.spanRef, h.trendRef, h.divRef, {
        drawnMarkers: [],
        rebuildToken: 'candles',
      }),
    )
    h.fire({ point: { x: 40, y: 20 }, seriesData: new Map() } as unknown as MouseEventParams)
    expect(result.current?.content.divergences?.[0]).toContain('Regular bearish divergence')
    // The glossary what-it-means line rides along after the em dash.
    expect(result.current?.content.divergences?.[0]).toContain('losing momentum')
  })
})
