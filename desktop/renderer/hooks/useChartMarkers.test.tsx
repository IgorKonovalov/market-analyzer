import { renderHook } from '@testing-library/react'
import { createSeriesMarkers } from 'lightweight-charts'
import type { SeriesMarker, UTCTimestamp } from 'lightweight-charts'

import { useChartMarkers } from './useChartMarkers'
import type { MainSeries } from '../lib/chartSeries'
import type { ChartMarker } from '../lib/markers'
import { PatternSpanPrimitive } from '../lib/spans'

function harness() {
  // v5 routes markers through the createSeriesMarkers plugin, not series.setMarkers.
  // Capture the plugin's setMarkers so the existing assertions hold unchanged.
  const setMarkers = jest.fn<void, [SeriesMarker<UTCTimestamp>[]]>()
  ;(createSeriesMarkers as jest.Mock).mockReturnValue({
    setMarkers,
    detach: jest.fn(),
    applyOptions: jest.fn(),
    markers: jest.fn(() => []),
  })
  const series = {} as unknown as MainSeries
  const setSpans = jest.fn()
  const setColors = jest.fn()
  const setVisible = jest.fn()
  const primitive = { setSpans, setColors, setVisible } as unknown as PatternSpanPrimitive
  const container = document.createElement('div')
  return { series, setMarkers, primitive, setSpans, setColors, setVisible, container }
}

// Two markers on distinct days; a span-bearing one to prove spans flow.
const MARKERS: ChartMarker[] = [
  { event_ts: '2026-04-10T00:00:00+00:00', kind: 'bullish_marker', pattern: 'hammer' },
  { event_ts: '2026-04-11T00:00:00+00:00', kind: 'neutral_marker', pattern: 'doji' },
]

describe('useChartMarkers', () => {
  it('draws the given markers and feeds the same set to the span band', () => {
    const h = harness()
    renderHook(() =>
      useChartMarkers(
        { current: h.series },
        { current: h.container },
        { current: h.primitive },
        {
          drawnMarkers: MARKERS,
          clickedBarTs: null,
          highlightedCandleGroup: null,
          effectiveTheme: 'light',
          styleVersion: 0,
          rebuildToken: 'candles',
        },
      ),
    )
    expect(h.setMarkers).toHaveBeenCalledTimes(1)
    expect(h.setMarkers.mock.calls[0][0]).toHaveLength(2)
    expect(h.setVisible).toHaveBeenCalledWith(true)
    expect(h.setSpans).toHaveBeenCalledTimes(1)
  })

  it('adds the clicked-bar circle affordance in ascending time order', () => {
    const h = harness()
    renderHook(() =>
      useChartMarkers(
        { current: h.series },
        { current: h.container },
        { current: h.primitive },
        {
          drawnMarkers: MARKERS,
          clickedBarTs: '2026-04-12T00:00:00+00:00', // after both markers
          highlightedCandleGroup: null,
          effectiveTheme: 'light',
          styleVersion: 0,
          rebuildToken: 'candles',
        },
      ),
    )
    const drawn = h.setMarkers.mock.calls[0][0]
    expect(drawn).toHaveLength(3) // 2 markers + the clicked circle
    // The clicked circle sits last (latest time) and is a circle.
    expect(drawn[2]).toMatchObject({ shape: 'circle' })
    // Ascending time order.
    const times = drawn.map((m) => m.time as number)
    expect([...times]).toEqual([...times].sort((a, b) => a - b))
  })

  it('clears markers and spans when nothing is drawn (all groups off / master hidden)', () => {
    const h = harness()
    renderHook(() =>
      useChartMarkers(
        { current: h.series },
        { current: h.container },
        { current: h.primitive },
        {
          drawnMarkers: [],
          clickedBarTs: null,
          highlightedCandleGroup: null,
          effectiveTheme: 'light',
          styleVersion: 0,
          rebuildToken: 'candles',
        },
      ),
    )
    expect(h.setMarkers).toHaveBeenCalledWith([])
    expect(h.setSpans).toHaveBeenCalledWith([])
  })
})
