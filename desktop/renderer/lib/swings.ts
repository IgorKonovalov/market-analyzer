/**
 * Client-side swing pivots + dominant-swing auto-anchor (Plan 0092 phase 5).
 *
 * A faithful mirror of `src/market_analyser/analysis/levels.py::swing_pivots` and
 * `analysis/fibonacci.py::dominant_swing` — the geometry the price-structure
 * overlays (`fibonacci` / `anchored_vwap`) auto-anchor to when the wire carries no
 * explicit anchor (the ADR-0077 client path). Display-only, outside the
 * determinism-critical backtest path; `swings.test.ts` pins it against the Python
 * reference. Trailing by construction: a pivot at bar `j` needs a full wing of
 * right-context, so no future bar beyond the series end is read.
 */
import type { Bar } from '../types/sidecar/bar'

export interface SwingPivot {
  barIndex: number
  ts: string
  price: number
  kind: 'high' | 'low'
}

/** Confirmed swing wings — the 3/3 window the snapshot's SR_PIVOT_WINDOW uses. */
export const DEFAULT_WING = 3
/** Only pivots within this trailing bar window feed the dominant-swing auto-anchor. */
export const DOMINANT_SWING_LOOKBACK = 120

/** Confirmed swing pivots, ordered by `barIndex` (a same-bar high before its low),
 * mirroring the Python `swing_pivots`: a `high` pivot strictly exceeds every high
 * in the `left`/`right` neighbourhood, a `low` pivot mirrors on lows. */
export function swingPivots(bars: Bar[], left = DEFAULT_WING, right = DEFAULT_WING): SwingPivot[] {
  const pivots: SwingPivot[] = []
  const n = bars.length
  for (let j = left; j < n - right; j++) {
    let isHigh = true
    let isLow = true
    for (let k = j - left; k <= j + right; k++) {
      if (k === j) continue
      if (bars[k].high >= bars[j].high) isHigh = false
      if (bars[k].low <= bars[j].low) isLow = false
    }
    if (isHigh) {
      pivots.push({ barIndex: j, ts: bars[j].event_ts, price: bars[j].high, kind: 'high' })
    }
    if (isLow) {
      pivots.push({ barIndex: j, ts: bars[j].event_ts, price: bars[j].low, kind: 'low' })
    }
  }
  return pivots
}

export interface SwingAnchors {
  high: SwingPivot
  low: SwingPivot
}

/** The dominant recent swing's `{high, low}` anchors, or `null`. The
 * largest-magnitude leg between two consecutive confirmed pivots of opposite kind
 * within the trailing `lookback` window (ties break toward the more recent leg) —
 * a mirror of the Python `dominant_swing`. */
export function dominantSwing(
  bars: Bar[],
  lookback = DOMINANT_SWING_LOOKBACK,
): SwingAnchors | null {
  let pivots = swingPivots(bars)
  if (bars.length > 0) {
    const cutoff = bars.length - lookback
    pivots = pivots.filter((p) => p.barIndex >= cutoff)
  }
  let bestSpan = -1
  let best: SwingAnchors | null = null
  for (let i = 0; i + 1 < pivots.length; i++) {
    const a = pivots[i]
    const b = pivots[i + 1]
    if (a.kind === b.kind || a.barIndex === b.barIndex) continue
    const span = Math.abs(a.price - b.price)
    if (span >= bestSpan) {
      // >= so a later equal-span leg wins the tie, matching the Python.
      bestSpan = span
      best = a.kind === 'high' ? { high: a, low: b } : { high: b, low: a }
    }
  }
  return best
}
