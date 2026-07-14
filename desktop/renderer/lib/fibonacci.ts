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

/** The resolved swing the grid is anchored to (Plan 0105 phase 5, ADR-0100 rule
 * 1) — display-only: the render draws the 0/1 anchor boundaries and states the
 * anchoring leg from these; the level *prices* never read them post-compute. */
export interface FibAnchors {
  highTs: string
  highPrice: number
  lowTs: string
  lowPrice: number
}

export interface FibGrid {
  kind: 'retracement' | 'extension'
  direction: 'bullish' | 'bearish'
  levels: FibLevel[]
  anchors: FibAnchors
}

/** The ratio's dict key, matching the Python `str(ratio)` (`2.0` ⇒ `"2.0"`, not the
 * JS `String(2)` ⇒ `"2"`). */
function ratioKey(ratio: number): string {
  return Number.isInteger(ratio) ? ratio.toFixed(1) : String(ratio)
}

interface ResolvedAnchors {
  anchors: FibAnchors
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
      anchors: {
        highTs: spec.high_anchor_ts,
        highPrice: spec.high_anchor_price,
        lowTs: spec.low_anchor_ts,
        lowPrice: spec.low_anchor_price,
      },
      // bullish = the low printed at-or-before the high (ISO strings compare
      // chronologically), matching the Python `_direction`.
      direction: spec.low_anchor_ts <= spec.high_anchor_ts ? 'bullish' : 'bearish',
    }
  }
  const swing = dominantSwing(bars)
  if (swing === null) return null
  return {
    anchors: {
      highTs: swing.high.ts,
      highPrice: swing.high.price,
      lowTs: swing.low.ts,
      lowPrice: swing.low.price,
    },
    direction: swing.low.ts <= swing.high.ts ? 'bullish' : 'bearish',
  }
}

/** The Fibonacci grid for a `fibonacci` overlay, or `null` when there is no swing
 * to anchor to. `retracement` (default) draws levels inside the swing; `extension`
 * projects them off the last close (mirroring the tool's pullback anchor). */
export function fibonacciGrid(bars: Bar[], spec: OverlaySpec): FibGrid | null {
  const resolved = resolveAnchors(bars, spec)
  if (resolved === null) return null
  const { anchors, direction } = resolved
  const { highPrice, lowPrice } = anchors
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
  return { kind, direction, levels, anchors }
}

/** One drawn 0/1 anchor boundary (Plan 0105 phase 5): a labeled horizontal line
 * at the swing endpoint, disclosing the anchoring leg. */
export interface FibAnchorLine {
  key: 'anchor0' | 'anchor1'
  price: number
  title: string
}

/**
 * The two swing-anchor boundary lines for a grid. Ratio 0 sits where the leg
 * ENDS (the retracement measures from there): the high of a bullish leg, the
 * low of a bearish one; ratio 1 is the leg's origin. An extension grid projects
 * its levels off the last close instead, so its anchors are titled as the
 * source swing rather than 0/1 endpoints. Pure — display labels only.
 */
export function fibAnchorLines(grid: FibGrid): FibAnchorLine[] {
  const { anchors, direction, kind } = grid
  const leg = `${direction} leg`
  const end =
    direction === 'bullish'
      ? { price: anchors.highPrice, side: 'high' }
      : { price: anchors.lowPrice, side: 'low' }
  const origin =
    direction === 'bullish'
      ? { price: anchors.lowPrice, side: 'low' }
      : { price: anchors.highPrice, side: 'high' }
  if (kind === 'extension') {
    return [
      { key: 'anchor0', price: end.price, title: `Fib anchor — ${leg} ${end.side}` },
      { key: 'anchor1', price: origin.price, title: `Fib anchor — ${leg} ${origin.side}` },
    ]
  }
  return [
    { key: 'anchor0', price: end.price, title: `Fib 0 — ${leg} ${end.side}` },
    { key: 'anchor1', price: origin.price, title: `Fib 1 — ${leg} ${origin.side}` },
  ]
}
