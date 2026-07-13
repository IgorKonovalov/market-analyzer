/**
 * Client-side market structure (Plan 0092 phase 6, ADR-0084).
 *
 * A faithful mirror of `src/market_analyser/analysis/structure.py` — the
 * price-action HH/HL/LH/LL labeling, the derived structural trend, and the
 * BOS/CHoCH events, computed from the bars the chart holds (the same
 * client-compute posture as the phase-5 fib/pivot overlays, ADR-0077). This is the
 * ADR-0084 *second, distinct* trend read; it is drawn alongside the price, never
 * merged into any indicator trend. Display-only; `marketStructure.test.ts` pins it
 * against the Python reference. Trailing by construction: labels and events read
 * only confirmed pivots and closes at-or-before their bar.
 */
import { DEFAULT_WING, swingPivots, type SwingPivot } from './swings'
import type { Bar } from '../types/sidecar/bar'

export type StructureLabel = 'HH' | 'HL' | 'LH' | 'LL'
export type StructureEventKind = 'BOS' | 'CHoCH'
export type StructuralTrend = 'up' | 'down' | 'range'

export interface LabeledPivot {
  pivot: SwingPivot
  label: StructureLabel
}

export interface StructureEvent {
  kind: StructureEventKind
  direction: 'bullish' | 'bearish'
  barIndex: number
  price: number
}

export interface MarketStructureResult {
  structuralTrend: StructuralTrend
  labeledPivots: LabeledPivot[]
  events: StructureEvent[]
}

/** Trailing ATR window + the ATR-scaled break margin — mirror of the Python
 * constants. */
export const ATR_PERIOD = 14
export const BOS_MARGIN_ATR = 0.25

/** Wilder ATR, seeded by the SMA of TR[1..period] at index `period` — a mirror of
 * `analysis/indicators.py::atr`. `null` until index `period`. */
function wilderAtr(bars: Bar[], period: number): Array<number | null> {
  const n = bars.length
  const out: Array<number | null> = new Array(n).fill(null)
  if (n <= period) return out
  const tr: Array<number | null> = new Array(n).fill(null)
  for (let i = 1; i < n; i++) {
    const { high, low } = bars[i]
    const prevClose = bars[i - 1].close
    tr[i] = Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose))
  }
  let seed = 0
  for (let i = 1; i <= period; i++) {
    const v = tr[i]
    if (v === null) return out
    seed += v
  }
  seed /= period
  out[period] = seed
  let prev = seed
  for (let i = period + 1; i < n; i++) {
    prev = (prev * (period - 1) + (tr[i] as number)) / period
    out[i] = prev
  }
  return out
}

/** Label each pivot with a same-kind predecessor (the first high/low is unlabeled),
 * mirroring the Python `_label_pivots`. */
function labelPivots(pivots: SwingPivot[]): LabeledPivot[] {
  const labeled: LabeledPivot[] = []
  let prevHigh: number | null = null
  let prevLow: number | null = null
  for (const p of pivots) {
    if (p.kind === 'high') {
      if (prevHigh !== null) labeled.push({ pivot: p, label: p.price > prevHigh ? 'HH' : 'LH' })
      prevHigh = p.price
    } else {
      if (prevLow !== null) labeled.push({ pivot: p, label: p.price > prevLow ? 'HL' : 'LL' })
      prevLow = p.price
    }
  }
  return labeled
}

/** Derive the structural trend from the latest high + low labels — up = HH+HL,
 * down = LH+LL, else range (mirror of `_structural_trend`). */
function deriveStructuralTrend(labeled: LabeledPivot[]): StructuralTrend {
  let lastHigh: StructureLabel | null = null
  let lastLow: StructureLabel | null = null
  for (let i = labeled.length - 1; i >= 0; i--) {
    const l = labeled[i].label
    if (lastHigh === null && (l === 'HH' || l === 'LH')) lastHigh = l
    if (lastLow === null && (l === 'HL' || l === 'LL')) lastLow = l
  }
  if (lastHigh === 'HH' && lastLow === 'HL') return 'up'
  if (lastHigh === 'LH' && lastLow === 'LL') return 'down'
  return 'range'
}

/** The HH/HL/LH/LL labeling, structural trend, and BOS/CHoCH events over `bars`. */
export function marketStructure(
  bars: Bar[],
  pivotWindow = DEFAULT_WING,
  bosMarginAtr = BOS_MARGIN_ATR,
  atrPeriod = ATR_PERIOD,
): MarketStructureResult {
  if (bars.length === 0) return { structuralTrend: 'range', labeledPivots: [], events: [] }

  const pivots = swingPivots(bars, pivotWindow, pivotWindow)
  const labeledPivots = labelPivots(pivots)

  // A pivot at bar j is usable as a reference once its right-context exists
  // (bar j + pivotWindow) — the same bar it becomes confirmed.
  const confirmedAt = new Map<number, SwingPivot[]>()
  for (const p of pivots) {
    const c = p.barIndex + pivotWindow
    const list = confirmedAt.get(c)
    if (list) list.push(p)
    else confirmedAt.set(c, [p])
  }

  const atrSeries = wilderAtr(bars, atrPeriod)
  const events: StructureEvent[] = []
  let bias: 'up' | 'down' | null = null
  let refHigh: SwingPivot | null = null
  let refLow: SwingPivot | null = null

  for (let i = 0; i < bars.length; i++) {
    for (const p of confirmedAt.get(i) ?? []) {
      if (p.kind === 'high') refHigh = p
      else refLow = p
    }
    const close = bars[i].close
    const atr = atrSeries[i]
    const margin = (atr ?? 0) * bosMarginAtr
    if (refHigh !== null && close > refHigh.price + margin) {
      // With bias down, an upside break is the first counter-trend break (CHoCH);
      // otherwise a BOS. No bias yet -> BOS.
      events.push({
        kind: bias === 'down' ? 'CHoCH' : 'BOS',
        direction: 'bullish',
        barIndex: i,
        price: refHigh.price,
      })
      bias = 'up'
      refHigh = null
    } else if (refLow !== null && close < refLow.price - margin) {
      events.push({
        kind: bias === 'up' ? 'CHoCH' : 'BOS',
        direction: 'bearish',
        barIndex: i,
        price: refLow.price,
      })
      bias = 'down'
      refLow = null
    }
  }

  return { structuralTrend: deriveStructuralTrend(labeledPivots), labeledPivots, events }
}
