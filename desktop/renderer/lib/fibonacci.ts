/**
 * Client-side Fibonacci grid (Plan 0092 phase 5).
 *
 * A faithful mirror of `src/market_analyser/analysis/fibonacci.py` — the
 * retracement / extension level prices a `fibonacci` overlay draws on the price
 * pane. Anchors come from the overlay's explicit swing (all four `*_anchor_*`
 * fields) or, when absent, the client `dominantSwing` auto-anchor (ADR-0077 client
 * path). Display-only; `fibonacci.test.ts` pins the level prices against the Python
 * reference within 1e-9.
 */
import { dominantSwing } from './swings'
import type { OverlaySpec } from '../types/events'
import type { Bar } from '../types/sidecar/bar'

export const RETRACEMENT_RATIOS = [0.236, 0.382, 0.5, 0.618, 0.786] as const
export const EXTENSION_RATIOS = [1.272, 1.618, 2.0, 2.618] as const

export interface FibLevel {
  ratio: string
  price: number
}

export interface FibGrid {
  kind: 'retracement' | 'extension'
  direction: 'bullish' | 'bearish'
  levels: FibLevel[]
}

/** The ratio's dict key, matching the Python `str(ratio)` (`2.0` ⇒ `"2.0"`, not the
 * JS `String(2)` ⇒ `"2"`). */
function ratioKey(ratio: number): string {
  return Number.isInteger(ratio) ? ratio.toFixed(1) : String(ratio)
}

interface ResolvedAnchors {
  highPrice: number
  lowPrice: number
  direction: 'bullish' | 'bearish'
}

function resolveAnchors(bars: Bar[], spec: OverlaySpec): ResolvedAnchors | null {
  if (
    spec.high_anchor_ts != null &&
    spec.high_anchor_price != null &&
    spec.low_anchor_ts != null &&
    spec.low_anchor_price != null
  ) {
    return {
      highPrice: spec.high_anchor_price,
      lowPrice: spec.low_anchor_price,
      // bullish = the low printed at-or-before the high (ISO strings compare
      // chronologically), matching the Python `_direction`.
      direction: spec.low_anchor_ts <= spec.high_anchor_ts ? 'bullish' : 'bearish',
    }
  }
  const swing = dominantSwing(bars)
  if (swing === null) return null
  return {
    highPrice: swing.high.price,
    lowPrice: swing.low.price,
    direction: swing.low.ts <= swing.high.ts ? 'bullish' : 'bearish',
  }
}

/** The Fibonacci grid for a `fibonacci` overlay, or `null` when there is no swing
 * to anchor to. `retracement` (default) draws levels inside the swing; `extension`
 * projects them off the last close (mirroring the tool's pullback anchor). */
export function fibonacciGrid(bars: Bar[], spec: OverlaySpec): FibGrid | null {
  const anchors = resolveAnchors(bars, spec)
  if (anchors === null) return null
  const { highPrice, lowPrice, direction } = anchors
  const span = highPrice - lowPrice
  const kind = spec.fib_kind ?? 'retracement'
  const levels: FibLevel[] = []
  if (kind === 'extension') {
    const base = bars.length > 0 ? bars[bars.length - 1].close : highPrice
    for (const r of EXTENSION_RATIOS) {
      levels.push({
        ratio: ratioKey(r),
        price: direction === 'bullish' ? base + r * span : base - r * span,
      })
    }
  } else {
    for (const r of RETRACEMENT_RATIOS) {
      levels.push({
        ratio: ratioKey(r),
        price: direction === 'bullish' ? highPrice - r * span : lowPrice + r * span,
      })
    }
  }
  return { kind, direction, levels }
}
