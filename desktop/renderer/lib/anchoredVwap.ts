/**
 * Client-side anchored VWAP (Plan 0092 phase 5).
 *
 * A faithful mirror of `src/market_analyser/analysis/volume.py::anchored_vwap` —
 * the running volume-weighted typical price accumulated from an anchor bar, drawn
 * as a line series on the price pane. The anchor is the overlay's explicit
 * `anchor_ts` (matched to a bar) or, when absent, the start of the client
 * `dominantSwing` (ADR-0077 client path). Display-only; `anchoredVwap.test.ts`
 * pins it against the Python reference within 1e-9.
 */
import type { LineData, UTCTimestamp } from 'lightweight-charts'

import { dominantSwing } from './swings'
import type { Bar } from '../types/sidecar/bar'

function toUtcSeconds(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp
}

/** The 0-based anchor bar index for an anchored VWAP: the exact/at-or-after bar for
 * an explicit `anchorTs`, else the dominant swing's earlier pivot, else the first
 * bar. */
export function resolveAnchorIndex(bars: Bar[], anchorTs?: string | null): number {
  if (bars.length === 0) return 0
  if (anchorTs != null) {
    const exact = bars.findIndex((b) => b.event_ts === anchorTs)
    if (exact >= 0) return exact
    const after = bars.findIndex((b) => b.event_ts >= anchorTs)
    return after >= 0 ? after : 0
  }
  const swing = dominantSwing(bars)
  if (swing === null) return 0
  const startTs = swing.high.ts <= swing.low.ts ? swing.high.ts : swing.low.ts
  const i = bars.findIndex((b) => b.event_ts === startTs)
  return i >= 0 ? i : 0
}

/** The anchored-VWAP line from the resolved anchor to the last bar. A bar whose
 * cumulative volume since the anchor is still `0` is skipped (undefined — never a
 * divide-by-zero), matching the Python guard. */
export function anchoredVwapSeries(bars: Bar[], anchorTs?: string | null): LineData[] {
  if (bars.length === 0) return []
  const anchorIdx = resolveAnchorIndex(bars, anchorTs)
  const out: LineData[] = []
  let cumVolume = 0
  let cumWeighted = 0
  for (let i = anchorIdx; i < bars.length; i++) {
    const b = bars[i]
    cumVolume += b.volume
    cumWeighted += ((b.high + b.low + b.close) / 3) * b.volume
    if (cumVolume === 0) continue
    out.push({ time: toUtcSeconds(b.event_ts), value: cumWeighted / cumVolume })
  }
  return out
}
