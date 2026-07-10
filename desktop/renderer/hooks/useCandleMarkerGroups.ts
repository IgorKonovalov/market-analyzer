/**
 * Candlestick-marker grouping state (Plan 0071 phase 2 — lifted verbatim out of
 * `CandlestickChart` in the Plan 0072 phase 8 decomposition, no behaviour change).
 *
 * Groups the sweep markers by (pattern type, direction) from the ADR-0045 identity
 * and owns the draw-on-select selection: only ENABLED groups draw, seeded to the
 * single most-recent group on each NEW sweep (never re-seeded on a live tick that
 * just grows an existing group). Exposes `drawnMarkers` (master on ⊗ group enabled),
 * per-group toggle, hover-highlight state, and the group-key set the component's
 * legend routing uses. All ephemeral, never persisted.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { CANDLE_MASTER_ID, groupCandlestickMarkers, mostRecentGroupKey } from '../lib/candleGroups'
import type { CandlestickPatternGroup } from '../lib/candleGroups'
import { candleGroupKey, type ChartMarker } from '../lib/markers'

export interface CandleMarkerGroups {
  candleGroups: CandlestickPatternGroup[]
  enabledCandleGroups: ReadonlySet<string>
  /** Markers actually drawn: master on AND the marker's group enabled. Feeds both
   * the marker draw and the span band, so they gate identically. */
  drawnMarkers: ChartMarker[]
  highlightedCandleGroup: string | null
  setHighlightedCandleGroup: (key: string | null) => void
  toggleCandleGroup: (key: string) => void
  /** The set of group keys present — the legend routing checks membership to
   * decide whether a layer id is a candlestick group (opt-in) or opt-out. */
  candleKeySet: ReadonlySet<string>
}

export function useCandleMarkerGroups(
  annotations: ChartMarker[] | undefined,
  hidden: ReadonlySet<string>,
): CandleMarkerGroups {
  // The sweep markers grouped by (pattern type, direction). Recomputed when the
  // markers change.
  const candleGroups = useMemo(() => groupCandlestickMarkers(annotations ?? []), [annotations])
  // Opt-in per-group visibility (draw-on-select), seeded to the most-recent group
  // on each NEW sweep (the effect below) so the chart is populated, not walled.
  const [enabledCandleGroups, setEnabledCandleGroups] = useState<ReadonlySet<string>>(
    () => new Set(),
  )
  // The (type, direction) group-set signature of the last render. Reseed the
  // enabled set only when the GROUPS change (a new sweep) — never on a live tick
  // that just grows an existing group, which would yank the user's picks.
  const prevCandleSigRef = useRef<string>('')
  useEffect(() => {
    const sig = candleGroups
      .map((g) => g.key)
      .sort()
      .join(',')
    if (sig === prevCandleSigRef.current) return
    prevCandleSigRef.current = sig
    const recent = mostRecentGroupKey(candleGroups)
    setEnabledCandleGroups(recent === null ? new Set() : new Set([recent]))
  }, [candleGroups])
  // Hovered candlestick legend group (its key), or null — emphasises that group's
  // markers and fades the rest.
  const [highlightedCandleGroup, setHighlightedCandleGroup] = useState<string | null>(null)
  // The candlestick layer MASTER toggle: master off hides every group's markers +
  // spans WITHOUT clearing the per-group selection (no desync). Opt-out via the
  // shared `hidden` set.
  const candleMasterHidden = hidden.has(CANDLE_MASTER_ID)
  const drawnMarkers = useMemo(
    () =>
      candleMasterHidden
        ? []
        : (annotations ?? []).filter((m) => enabledCandleGroups.has(candleGroupKey(m))),
    [annotations, candleMasterHidden, enabledCandleGroups],
  )
  const toggleCandleGroup = useCallback((key: string): void => {
    setEnabledCandleGroups((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])
  const candleKeySet = useMemo(() => new Set(candleGroups.map((g) => g.key)), [candleGroups])

  return {
    candleGroups,
    enabledCandleGroups,
    drawnMarkers,
    highlightedCandleGroup,
    setHighlightedCandleGroup,
    toggleCandleGroup,
    candleKeySet,
  }
}
