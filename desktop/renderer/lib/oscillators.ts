/**
 * Momentum-oscillator math for chart sub-panes (Plan 0091 phase 6).
 *
 * Pure functions — no React, no Node, no canvas. Faithful mirrors of the Python
 * `src/market_analyser/analysis/indicators.py` oscillators (`stochastic`,
 * `stochastic_rsi`, `cci`, `williams_r`, `roc`), each pinned against the Python
 * reference within 1e-6 in `oscillators.test.ts`. Inputs are `Bar` records;
 * outputs are `lightweight-charts` `LineData` (UTC-seconds time + numeric value),
 * emitting only the *defined* points (the leading undefined region is dropped, as
 * `computeBbands` does) so a series can be fed straight to `series.setData(...)`.
 *
 * No lookahead bias: every value at index `i` is computed from `bars[0..=i]` only
 * (defended by the truncation-invariance tests). Degenerate windows are skipped
 * (a flat high/low window, a flat RSI window, a zero-mean-deviation window, a zero
 * reference close) exactly as the Python guards return `None` — never a NaN or
 * Infinity reaching lightweight-charts.
 */
import type { LineData, UTCTimestamp } from 'lightweight-charts'

import type { Bar } from '../types/sidecar/bar'

function tsOf(bar: Bar): UTCTimestamp {
  return Math.floor(new Date(bar.event_ts).getTime() / 1000) as UTCTimestamp
}

/** A Stochastic reading: the fast `%K` line and its `%D` smoothing, sharing times. */
export interface StochasticData {
  k: LineData[]
  d: LineData[]
}

/**
 * Fast Stochastic oscillator — mirror of `indicators.py::stochastic`. Raw
 * `%K = 100 * (close - lowest_low) / (highest_high - lowest_low)` over the trailing
 * `kPeriod` bars; `%D` is the `dPeriod`-SMA of `%K`. A flat window
 * (`highest_high === lowest_low`) leaves `%K` undefined and is skipped. Both lines
 * share the times where the value object is defined. Defaults 14 / 3.
 */
export function computeStochastic(
  bars: ReadonlyArray<Bar>,
  kPeriod = 14,
  dPeriod = 3,
): StochasticData {
  if (kPeriod < 1 || dPeriod < 1) return { k: [], d: [] }
  const n = bars.length
  const rawK: Array<number | null> = new Array<number | null>(n).fill(null)
  for (let i = kPeriod - 1; i < n; i++) {
    let hh = -Infinity
    let ll = Infinity
    for (let j = i - kPeriod + 1; j <= i; j++) {
      if (bars[j].high > hh) hh = bars[j].high
      if (bars[j].low < ll) ll = bars[j].low
    }
    const range = hh - ll
    if (range === 0) continue
    rawK[i] = (100 * (bars[i].close - ll)) / range
  }

  const k: LineData[] = []
  const d: LineData[] = []
  for (let i = dPeriod - 1; i < n; i++) {
    const kv = rawK[i]
    if (kv === null) continue
    let sum = 0
    let ok = true
    for (let j = i - dPeriod + 1; j <= i; j++) {
      const v = rawK[j]
      if (v === null) {
        ok = false
        break
      }
      sum += v
    }
    if (!ok) continue
    const time = tsOf(bars[i])
    k.push({ time, value: kv })
    d.push({ time, value: sum / dPeriod })
  }
  return { k, d }
}

/** Wilder RSI series aligned to `closes`, `null` until index `period` — the
 * internal input to Stochastic RSI, matching `indicators.py::rsi`. */
function rsiSeries(closes: ReadonlyArray<number>, period: number): Array<number | null> {
  const n = closes.length
  const out: Array<number | null> = new Array<number | null>(n).fill(null)
  if (period < 1 || n <= period) return out
  const rsiFrom = (avgGain: number, avgLoss: number): number =>
    avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss)

  let gains = 0
  let losses = 0
  for (let i = 1; i <= period; i++) {
    const change = closes[i] - closes[i - 1]
    if (change >= 0) gains += change
    else losses += -change
  }
  let avgGain = gains / period
  let avgLoss = losses / period
  out[period] = rsiFrom(avgGain, avgLoss)
  for (let i = period + 1; i < n; i++) {
    const change = closes[i] - closes[i - 1]
    const gain = change > 0 ? change : 0
    const loss = change < 0 ? -change : 0
    avgGain = (avgGain * (period - 1) + gain) / period
    avgLoss = (avgLoss * (period - 1) + loss) / period
    out[i] = rsiFrom(avgGain, avgLoss)
  }
  return out
}

/**
 * Stochastic RSI — mirror of `indicators.py::stochastic_rsi`. The Stochastic `%K`
 * formula applied to the RSI series rather than price, scaled 0-100:
 * `100 * (rsi - min(rsi)) / (max(rsi) - min(rsi))` over the trailing `stochPeriod`
 * RSI values. A flat RSI window is skipped. Defaults 14 / 14.
 */
export function computeStochasticRsi(
  bars: ReadonlyArray<Bar>,
  rsiPeriod = 14,
  stochPeriod = 14,
): LineData[] {
  if (stochPeriod < 1) return []
  const closes = bars.map((b) => b.close)
  const rsi = rsiSeries(closes, rsiPeriod)
  const out: LineData[] = []
  for (let i = stochPeriod - 1; i < bars.length; i++) {
    const cur = rsi[i]
    if (cur === null) continue
    let hi = -Infinity
    let lo = Infinity
    let ok = true
    for (let j = i - stochPeriod + 1; j <= i; j++) {
      const v = rsi[j]
      if (v === null) {
        ok = false
        break
      }
      if (v > hi) hi = v
      if (v < lo) lo = v
    }
    if (!ok) continue
    const range = hi - lo
    if (range === 0) continue
    out.push({ time: tsOf(bars[i]), value: (100 * (cur - lo)) / range })
  }
  return out
}

/**
 * Commodity Channel Index — mirror of `indicators.py::cci`. Typical price
 * `TP = (high + low + close) / 3`; `(TP - SMA(TP)) / (0.015 * mean_abs_deviation)`
 * over the trailing `period` bars. A zero mean deviation (flat `TP` window) is
 * skipped. Default 20.
 */
export function computeCci(bars: ReadonlyArray<Bar>, period = 20): LineData[] {
  if (period < 1 || bars.length < period) return []
  const tp = bars.map((b) => (b.high + b.low + b.close) / 3)
  const out: LineData[] = []
  for (let i = period - 1; i < bars.length; i++) {
    let sum = 0
    for (let j = i - period + 1; j <= i; j++) sum += tp[j]
    const mean = sum / period
    let meanDev = 0
    for (let j = i - period + 1; j <= i; j++) meanDev += Math.abs(tp[j] - mean)
    meanDev /= period
    if (meanDev === 0) continue
    out.push({ time: tsOf(bars[i]), value: (tp[i] - mean) / (0.015 * meanDev) })
  }
  return out
}

/**
 * Williams %R — mirror of `indicators.py::williams_r`. `-100 * (highest_high -
 * close) / (highest_high - lowest_low)` over the trailing `period` bars, ranged
 * -100..0. A flat window is skipped. Default 14.
 */
export function computeWilliamsR(bars: ReadonlyArray<Bar>, period = 14): LineData[] {
  if (period < 1 || bars.length < period) return []
  const out: LineData[] = []
  for (let i = period - 1; i < bars.length; i++) {
    let hh = -Infinity
    let ll = Infinity
    for (let j = i - period + 1; j <= i; j++) {
      if (bars[j].high > hh) hh = bars[j].high
      if (bars[j].low < ll) ll = bars[j].low
    }
    const range = hh - ll
    if (range === 0) continue
    out.push({ time: tsOf(bars[i]), value: (-100 * (hh - bars[i].close)) / range })
  }
  return out
}

/**
 * Rate of Change — mirror of `indicators.py::roc`. `100 * (close[i] -
 * close[i - period]) / close[i - period]`, defined from index `period`. A zero
 * reference close is skipped. Default 12.
 */
export function computeRoc(bars: ReadonlyArray<Bar>, period = 12): LineData[] {
  if (period < 1) return []
  const closes = bars.map((b) => b.close)
  const out: LineData[] = []
  for (let i = period; i < bars.length; i++) {
    const prev = closes[i - period]
    if (prev === 0) continue
    out.push({ time: tsOf(bars[i]), value: (100 * (closes[i] - prev)) / prev })
  }
  return out
}
