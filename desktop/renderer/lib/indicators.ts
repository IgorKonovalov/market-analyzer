/**
 * Indicator math for chart overlays (Plan 0007 phase 4.5).
 *
 * Pure functions — no React, no Node, no canvas. Inputs are an array of
 * `Bar` records (which already includes ISO `event_ts`); outputs are an
 * array of `lightweight-charts` `LineData` (UTC-seconds time + numeric
 * value). The chart layer feeds these to `series.setData(...)`.
 *
 * No lookahead bias: every value at index `i` is computed from
 * `bars[0..=i]` only. That matches the project-wide rule and applies to
 * indicator math whether the use case is rendering (here) or backtesting.
 *
 * MVP scope is `ema` and `sma`; `bbands` (Plan 0082) is here too. `rsi`/`macd`
 * remain reserved `OverlayKind` values in the typed envelope schema but are not
 * rendered yet; the chart layer logs-and-skips those.
 */
import type { LineData, UTCTimestamp } from 'lightweight-charts'

import type { Bar } from '../types/sidecar/bar'

function tsOf(bar: Bar): UTCTimestamp {
  return Math.floor(new Date(bar.event_ts).getTime() / 1000) as UTCTimestamp
}

/**
 * Exponential Moving Average. Seeded with the SMA of the first `period`
 * closes; subsequent values use the standard recurrence
 * `ema_i = (close_i - ema_{i-1}) * k + ema_{i-1}` with `k = 2 / (period + 1)`.
 *
 * Returns an empty array if `period <= 0` or `bars.length < period`.
 */
export function computeEma(bars: ReadonlyArray<Bar>, period: number): LineData[] {
  if (period <= 0 || bars.length < period) return []
  const multiplier = 2 / (period + 1)
  const result: LineData[] = []

  let seed = 0
  for (let i = 0; i < period; i++) seed += bars[i].close
  seed /= period
  result.push({ time: tsOf(bars[period - 1]), value: seed })

  let prev = seed
  for (let i = period; i < bars.length; i++) {
    const next = (bars[i].close - prev) * multiplier + prev
    result.push({ time: tsOf(bars[i]), value: next })
    prev = next
  }
  return result
}

/**
 * Simple Moving Average over a rolling window of `period` closes. Returns
 * an empty array if `period <= 0` or `bars.length < period`.
 */
export function computeSma(bars: ReadonlyArray<Bar>, period: number): LineData[] {
  if (period <= 0 || bars.length < period) return []
  const result: LineData[] = []
  let sum = 0
  for (let i = 0; i < bars.length; i++) {
    sum += bars[i].close
    if (i >= period) sum -= bars[i - period].close
    if (i >= period - 1) {
      result.push({ time: tsOf(bars[i]), value: sum / period })
    }
  }
  return result
}

/** A Bollinger Bands reading: three parallel line series (upper / middle /
 * lower) sharing the same times, each defined from index `period - 1` onward. */
export interface BbandsData {
  upper: LineData[]
  middle: LineData[]
  lower: LineData[]
}

/**
 * Bollinger Bands — a faithful mirror of
 * `src/market_analyser/analysis/indicators.py::bollinger`: an SMA middle band with
 * outer bands at `k` **population** standard deviations (variance denominator is
 * `period`, not `period - 1`). The value at index `i` uses the trailing window
 * `bars[i - period + 1 .. i]` only (no lookahead) and is defined from index
 * `period - 1` onward. Defaults mirror the Python signature: `period = 20`,
 * `k = 2.0`. `indicators.test.ts` pins this against the Python reference within 1e-6.
 *
 * Returns three empty arrays when `period < 1` or `bars.length < period`.
 */
export function computeBbands(bars: ReadonlyArray<Bar>, period = 20, k = 2): BbandsData {
  if (period < 1 || bars.length < period) return { upper: [], middle: [], lower: [] }
  const upper: LineData[] = []
  const middle: LineData[] = []
  const lower: LineData[] = []
  for (let i = period - 1; i < bars.length; i++) {
    let sum = 0
    for (let j = i - period + 1; j <= i; j++) sum += bars[j].close
    const mean = sum / period
    let variance = 0
    for (let j = i - period + 1; j <= i; j++) {
      const diff = bars[j].close - mean
      variance += diff * diff
    }
    variance /= period // population stdev, per the Python reference
    const sd = Math.sqrt(variance)
    const time = tsOf(bars[i])
    upper.push({ time, value: mean + k * sd })
    middle.push({ time, value: mean })
    lower.push({ time, value: mean - k * sd })
  }
  return { upper, middle, lower }
}
