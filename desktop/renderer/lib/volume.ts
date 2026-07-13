/**
 * Volume math for the chart's volume pane + VWAP/OBV overlays (Plan 0027 phase 3).
 *
 * Pure functions — no React, no Node, no canvas. Inputs are `Bar` records
 * (ISO `event_ts`, OHLCV); outputs are `lightweight-charts` data (UTC-seconds
 * time + numeric value). The chart layer feeds these to `series.setData(...)`.
 *
 * No lookahead bias: every value at index `i` is computed from `bars[0..=i]`
 * only — the project-wide rule, the same discipline `lib/indicators.ts` keeps.
 *
 * Presentation-only: this duplicates `analysis/volume.py` (just as
 * `lib/indicators.ts` duplicates the Python EMA/SMA), so the chart can derive
 * series from the `bars` it already holds without a round-trip. The
 * AUTHORITATIVE volume read comes from `analyze_symbol` / the snapshot; these two
 * copies need not agree to the cent.
 *
 * VWAP here is a ROLLING TRAILING N-period volume-weighted average of the typical
 * price `(high + low + close) / 3` — NOT session-anchored VWAP. Our bars are
 * predominantly daily with no intraday session boundaries, so a session reset is
 * ill-defined; a rolling window is deterministic and trailing on any timeframe
 * (Plan 0027 "VWAP anchoring" decision; mirrors `analysis/volume.py`).
 */
import type { HistogramData, LineData, UTCTimestamp } from 'lightweight-charts'

import type { Bar } from '../types/sidecar/bar'

// Defaults mirror analysis/volume.py (VOLUME_SMA_PERIOD / VWAP_PERIOD = 20).
export const VOLUME_MA_PERIOD = 20
export const VWAP_PERIOD = 20
// Money-flow defaults mirror analysis/volume.py (Plan 0091 phase 7).
export const MFI_PERIOD = 14
export const CMF_PERIOD = 20

// Volume bars are tinted by candle direction. Semi-transparent so the histogram
// reads as a backdrop band rather than competing with the candlesticks; the hues
// match the chart's BULLISH/BEARISH candle colors.
export const VOLUME_BULLISH_COLOR = 'rgba(22, 163, 74, 0.5)' // close >= open
export const VOLUME_BEARISH_COLOR = 'rgba(220, 38, 38, 0.5)' // close < open

function tsOf(bar: Bar): UTCTimestamp {
  return Math.floor(new Date(bar.event_ts).getTime() / 1000) as UTCTimestamp
}

/**
 * Per-bar volume histogram data, each bar tinted bullish when `close >= open`
 * and bearish otherwise. One point per bar; empty when `bars` is empty.
 */
export function computeVolumeBars(bars: ReadonlyArray<Bar>): HistogramData[] {
  return bars.map((b) => ({
    time: tsOf(b),
    value: b.volume,
    color: b.close >= b.open ? VOLUME_BULLISH_COLOR : VOLUME_BEARISH_COLOR,
  }))
}

/**
 * Trailing simple moving average of volume over a rolling window of `period`
 * bars inclusive of `i`. Returns an empty array if `period <= 0` or
 * `bars.length < period` (mirrors `computeSma` in `lib/indicators.ts`).
 */
export function computeVolumeMa(bars: ReadonlyArray<Bar>, period: number): LineData[] {
  if (period <= 0 || bars.length < period) return []
  const result: LineData[] = []
  let sum = 0
  for (let i = 0; i < bars.length; i++) {
    sum += bars[i].volume
    if (i >= period) sum -= bars[i - period].volume
    if (i >= period - 1) {
      result.push({ time: tsOf(bars[i]), value: sum / period })
    }
  }
  return result
}

/**
 * Rolling trailing VWAP of the typical price `(high + low + close) / 3` over the
 * trailing `period` bars inclusive of `i`. Returns an empty array if
 * `period <= 0` or `bars.length < period`. A window whose total volume is `0` is
 * skipped (no point emitted) — the weighting is undefined there, and pushing an
 * `Infinity`/`NaN` into lightweight-charts would corrupt the price scale.
 */
export function computeVwap(bars: ReadonlyArray<Bar>, period: number): LineData[] {
  if (period <= 0 || bars.length < period) return []
  const result: LineData[] = []
  for (let i = period - 1; i < bars.length; i++) {
    let volumeSum = 0
    let weighted = 0
    for (let j = i - period + 1; j <= i; j++) {
      const typical = (bars[j].high + bars[j].low + bars[j].close) / 3
      volumeSum += bars[j].volume
      weighted += typical * bars[j].volume
    }
    if (volumeSum === 0) continue // degenerate zero-volume window — skip
    result.push({ time: tsOf(bars[i]), value: weighted / volumeSum })
  }
  return result
}

/**
 * Cumulative on-balance volume, seeded at `0` on the first bar. From `i >= 1`,
 * the bar's volume is added when `close > prev_close`, subtracted when
 * `close < prev_close`, and unchanged on a flat close. One point per bar; empty
 * when `bars` is empty. Mirrors `analysis/volume.py::obv`.
 */
export function computeObv(bars: ReadonlyArray<Bar>): LineData[] {
  if (bars.length === 0) return []
  const result: LineData[] = [{ time: tsOf(bars[0]), value: 0 }]
  let cumulative = 0
  for (let i = 1; i < bars.length; i++) {
    if (bars[i].close > bars[i - 1].close) cumulative += bars[i].volume
    else if (bars[i].close < bars[i - 1].close) cumulative -= bars[i].volume
    result.push({ time: tsOf(bars[i]), value: cumulative })
  }
  return result
}

/**
 * Money Flow Index — a volume-weighted RSI over the trailing `period` bars.
 * Mirror of `analysis/volume.py::mfi`: raw money flow is the typical price
 * `(high + low + close) / 3` times volume; `MFI = 100 * positive / (positive +
 * negative)` where a bar's flow is positive/negative as its typical price rose /
 * fell from the prior bar. `None`/skip for `i < period` and for a wholly flat
 * typical-price window (no directional flow). Default 14.
 */
export function computeMfi(bars: ReadonlyArray<Bar>, period = MFI_PERIOD): LineData[] {
  if (period < 1 || bars.length <= period) return []
  const tp = bars.map((b) => (b.high + b.low + b.close) / 3)
  const raw = bars.map((b, i) => tp[i] * b.volume)
  const out: LineData[] = []
  for (let i = period; i < bars.length; i++) {
    let positive = 0
    let negative = 0
    for (let j = i - period + 1; j <= i; j++) {
      if (tp[j] > tp[j - 1]) positive += raw[j]
      else if (tp[j] < tp[j - 1]) negative += raw[j]
    }
    const denom = positive + negative
    if (denom === 0) continue
    out.push({ time: tsOf(bars[i]), value: (100 * positive) / denom })
  }
  return out
}

/** One bar's Chaikin money-flow volume — the money-flow multiplier
 * `((close - low) - (high - close)) / (high - low)` times volume; a zero-range bar
 * contributes `0`. Mirror of `analysis/volume.py::_money_flow_volume`. */
function moneyFlowVolume(b: Bar): number {
  const range = b.high - b.low
  if (range === 0) return 0
  const multiplier = (b.close - b.low - (b.high - b.close)) / range
  return multiplier * b.volume
}

/**
 * Cumulative Accumulation/Distribution line — mirror of
 * `analysis/volume.py::accumulation_distribution`. Each bar adds its money-flow
 * volume to the running total; dense from bar 0 (a zero-range bar contributes 0).
 * One point per bar; empty when `bars` is empty.
 */
export function computeAccumulationDistribution(bars: ReadonlyArray<Bar>): LineData[] {
  if (bars.length === 0) return []
  const out: LineData[] = []
  let cumulative = 0
  for (let i = 0; i < bars.length; i++) {
    cumulative += moneyFlowVolume(bars[i])
    out.push({ time: tsOf(bars[i]), value: cumulative })
  }
  return out
}

/**
 * Chaikin Money Flow — mirror of `analysis/volume.py::chaikin_money_flow`. The
 * trailing `period`-bar sum of money-flow volume divided by the trailing volume
 * sum, a zero-centred read in `[-1, 1]`. `None`/skip for `i < period - 1` and for
 * a zero-volume window. Default 20.
 */
export function computeChaikinMoneyFlow(bars: ReadonlyArray<Bar>, period = CMF_PERIOD): LineData[] {
  if (period < 1 || bars.length < period) return []
  const out: LineData[] = []
  for (let i = period - 1; i < bars.length; i++) {
    let volumeSum = 0
    let flowSum = 0
    for (let j = i - period + 1; j <= i; j++) {
      volumeSum += bars[j].volume
      flowSum += moneyFlowVolume(bars[j])
    }
    if (volumeSum === 0) continue
    out.push({ time: tsOf(bars[i]), value: flowSum / volumeSum })
  }
  return out
}
