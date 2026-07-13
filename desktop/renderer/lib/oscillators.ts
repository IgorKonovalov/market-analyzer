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

/**
 * Wilder RSI as a drawable series — mirror of `indicators.py::rsi` (0-100),
 * emitting only the defined points from index `period` onward (the leading
 * `None` region dropped, as the other oscillators do). Feeds the RSI sub-pane
 * (Plan 0091 phase 9) and hosts price↔RSI divergence segments. Default 14.
 */
export function computeRsi(bars: ReadonlyArray<Bar>, period = 14): LineData[] {
  if (period < 1) return []
  const rsi = rsiSeries(
    bars.map((b) => b.close),
    period,
  )
  const out: LineData[] = []
  for (let i = 0; i < bars.length; i++) {
    const v = rsi[i]
    if (v === null) continue
    out.push({ time: tsOf(bars[i]), value: v })
  }
  return out
}

/**
 * EMA over a possibly-`null`-prefixed value sequence — faithful mirror of
 * `indicators.py::ema`: SMA-seed the first `period` consecutive defined values,
 * then advance by `alpha = 2 / (period + 1)`; collapse back to `null` on any
 * interior gap. Used by `computeMacdHist` for the fast/slow EMAs (over closes)
 * and the signal EMA (over the `null`-prefixed MACD line), so the histogram
 * matches Python within 1e-6.
 */
function emaOverValues(values: ReadonlyArray<number | null>, period: number): Array<number | null> {
  const n = values.length
  const out: Array<number | null> = new Array<number | null>(n).fill(null)
  if (period < 1) return out
  let firstDefined = -1
  for (let j = 0; j < n; j++) {
    if (values[j] !== null) {
      firstDefined = j
      break
    }
  }
  if (firstDefined === -1) return out
  const seedEnd = firstDefined + period - 1
  if (seedEnd >= n) return out
  let seedSum = 0
  for (let j = firstDefined; j <= seedEnd; j++) {
    const v = values[j]
    if (v === null) return out
    seedSum += v
  }
  const seed = seedSum / period
  out[seedEnd] = seed
  const alpha = 2 / (period + 1)
  let prev = seed
  for (let i = seedEnd + 1; i < n; i++) {
    const v = values[i]
    if (v === null) return out
    const curr = alpha * v + (1 - alpha) * prev
    out[i] = curr
    prev = curr
  }
  return out
}

/**
 * MACD histogram as a drawable series — mirror of `indicators.py::macd`'s
 * `histogram` (`(EMA(fast) - EMA(slow)) - signal-EMA`). Defined from index
 * `(slow - 1) + (signal - 1)` onward; earlier bars dropped. `fast` must be
 * strictly less than `slow`. This is the series the MACD sub-pane draws and the
 * one price↔MACD divergence (`oscillator = "macd_hist"`) pivots against.
 * Defaults 12 / 26 / 9.
 */
export function computeMacdHist(
  bars: ReadonlyArray<Bar>,
  fast = 12,
  slow = 26,
  signal = 9,
): LineData[] {
  if (fast < 1 || slow < 1 || signal < 1 || fast >= slow) return []
  const closes: Array<number | null> = bars.map((b) => b.close)
  const fastEma = emaOverValues(closes, fast)
  const slowEma = emaOverValues(closes, slow)
  const macdLine: Array<number | null> = closes.map((_, i) => {
    const f = fastEma[i]
    const s = slowEma[i]
    return f === null || s === null ? null : f - s
  })
  const signalLine = emaOverValues(macdLine, signal)
  const out: LineData[] = []
  for (let i = 0; i < bars.length; i++) {
    const m = macdLine[i]
    const sg = signalLine[i]
    if (m === null || sg === null) continue
    out.push({ time: tsOf(bars[i]), value: m - sg })
  }
  return out
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
