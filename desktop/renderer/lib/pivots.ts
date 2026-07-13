/**
 * Client-side classic pivot points (Plan 0092 phase 5).
 *
 * A faithful mirror of `src/market_analyser/analysis/levels.py::pivot_points` —
 * the P / R1-3 / S1-3 levels a `pivot_points` overlay draws as horizontal lines,
 * computed from the last completed bar's HLC. Display-only; `pivots.test.ts` pins
 * each method against the Python reference within 1e-9.
 */
import type { Bar } from '../types/sidecar/bar'

export type PivotMethod = 'floor' | 'camarilla' | 'woodie'

export interface PivotLevels {
  method: PivotMethod
  pivot: number
  resistances: number[] // R1, R2, R3
  supports: number[] // S1, S2, S3
}

/** Classic pivot levels from the last bar's HLC, or `null` for empty bars. Three
 * methods, each a hand-verifiable formula set mirroring the Python. */
export function pivotPoints(bars: Bar[], method: PivotMethod = 'floor'): PivotLevels | null {
  if (bars.length === 0) return null
  const last = bars[bars.length - 1]
  const { high, low, close } = last
  const rng = high - low

  if (method === 'camarilla') {
    const pivot = (high + low + close) / 3
    const resistances = [12, 6, 4].map((d) => close + (rng * 1.1) / d)
    const supports = [12, 6, 4].map((d) => close - (rng * 1.1) / d)
    return { method, pivot, resistances, supports }
  }

  const pivot = method === 'woodie' ? (high + low + 2 * close) / 4 : (high + low + close) / 3
  const resistances = [2 * pivot - low, pivot + rng, high + 2 * (pivot - low)]
  const supports = [2 * pivot - high, pivot - rng, low - 2 * (high - pivot)]
  return { method, pivot, resistances, supports }
}

/** The pivot levels flattened to labeled `{label, price}` rows in draw order
 * (S3..S1, P, R1..R3), the horizontal lines the overlay strokes. */
export function pivotLevelLines(levels: PivotLevels): Array<{ label: string; price: number }> {
  return [
    { label: 'S3', price: levels.supports[2] },
    { label: 'S2', price: levels.supports[1] },
    { label: 'S1', price: levels.supports[0] },
    { label: 'P', price: levels.pivot },
    { label: 'R1', price: levels.resistances[0] },
    { label: 'R2', price: levels.resistances[1] },
    { label: 'R3', price: levels.resistances[2] },
  ]
}
