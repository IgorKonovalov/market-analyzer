import { act, renderHook } from '@testing-library/react'

import { useCandleMarkerGroups } from './useCandleMarkerGroups'
import { CANDLE_MASTER_ID } from '../lib/candleGroups'
import type { ChartMarker } from '../lib/markers'

// 3 hammer (bullish) older, 2 doji (neutral) newest — two groups over 5 markers.
const SWEEP: ChartMarker[] = [
  { event_ts: '2026-04-10T00:00:00+00:00', kind: 'bullish_marker', pattern: 'hammer' },
  { event_ts: '2026-04-11T00:00:00+00:00', kind: 'bullish_marker', pattern: 'hammer' },
  { event_ts: '2026-04-12T00:00:00+00:00', kind: 'bullish_marker', pattern: 'hammer' },
  { event_ts: '2026-04-20T00:00:00+00:00', kind: 'neutral_marker', pattern: 'doji' },
  { event_ts: '2026-04-21T00:00:00+00:00', kind: 'neutral_marker', pattern: 'doji' },
]

describe('useCandleMarkerGroups', () => {
  it('groups by (pattern, direction) and defaults to drawing only the most-recent group', () => {
    const { result } = renderHook(() => useCandleMarkerGroups(SWEEP, new Set()))
    expect(result.current.candleGroups).toHaveLength(2)
    // Default draws only the 2 doji (most-recent), never all 5.
    expect(result.current.drawnMarkers).toHaveLength(2)
    expect(result.current.drawnMarkers.every((m) => m.pattern === 'doji')).toBe(true)
    expect(result.current.candleKeySet.has('hammer|bullish_marker')).toBe(true)
  })

  it('toggling a group on adds exactly that group; off removes it', () => {
    const { result } = renderHook(() => useCandleMarkerGroups(SWEEP, new Set()))
    act(() => result.current.toggleCandleGroup('hammer|bullish_marker'))
    expect(result.current.drawnMarkers).toHaveLength(5) // doji (2) + hammer (3)
    act(() => result.current.toggleCandleGroup('hammer|bullish_marker'))
    expect(result.current.drawnMarkers).toHaveLength(2)
  })

  it('the master toggle (hidden set) hides everything without clearing the per-group selection', () => {
    const { result, rerender } = renderHook(
      ({ hidden }: { hidden: Set<string> }) => useCandleMarkerGroups(SWEEP, hidden),
      { initialProps: { hidden: new Set<string>() } },
    )
    expect(result.current.drawnMarkers).toHaveLength(2)
    rerender({ hidden: new Set([CANDLE_MASTER_ID]) })
    expect(result.current.drawnMarkers).toHaveLength(0)
    // Un-hide → the preserved doji selection redraws (no desync).
    rerender({ hidden: new Set() })
    expect(result.current.drawnMarkers).toHaveLength(2)
  })

  it('does not re-seed the selection on a live tick that only grows an existing group', () => {
    const { result, rerender } = renderHook(
      ({ markers }: { markers: ChartMarker[] }) => useCandleMarkerGroups(markers, new Set()),
      { initialProps: { markers: SWEEP } },
    )
    // Enable hammer too.
    act(() => result.current.toggleCandleGroup('hammer|bullish_marker'))
    expect(result.current.drawnMarkers).toHaveLength(5)
    // A live tick adds one more doji — SAME group set, so the enabled set is kept.
    rerender({
      markers: [
        ...SWEEP,
        { event_ts: '2026-04-22T00:00:00+00:00', kind: 'neutral_marker', pattern: 'doji' },
      ],
    })
    expect(result.current.drawnMarkers).toHaveLength(6) // still both groups enabled
  })
})
