/**
 * Candlestick-pattern marker grouping (Plan 0071 phase 2). The `scan_patterns`
 * sweep (ADR-0045) delivers every in-view candlestick formation as a marker
 * carrying its pattern name + direction; painting them all at once buries the
 * candles (104 markers over ~520 daily AERO bars is ~one arrow per candle). This
 * module derives, entirely in the renderer, one group per (pattern type,
 * direction) so the legend can list them with counts and the chart draws only
 * the ENABLED groups — the same grouped-legend interaction Plan 0067 brought to
 * trendlines (ADR-0061), generalised to candlestick markers.
 *
 * Pure: no React, no chart. Inputs are `ChartMarker`s; outputs are group
 * descriptors + id helpers the component wires into the shared `LayersPanel`.
 * Legend labels stay plain-English here (the lib layer is exempt from the
 * `no-unkeyed-literals` guard), matching the existing marker/span/trendline
 * legend labels which Plan 0069 left un-localised by the same precedent.
 */
import type { MarkerKind } from '../types/events'
import { candleGroupKey, type ChartMarker } from './markers'

/** Legend-row / hidden-set id namespace for the candlestick layer. The MASTER
 * governs the whole layer; per-group rows namespace under `candles:` (never
 * colliding with the master's `candles-master` or an overlay/marker/pline id). */
export const CANDLE_LAYER_ID = 'candles'
export const CANDLE_MASTER_ID = 'candles-master'
export const CANDLE_MASTER_LABEL = 'Candlestick patterns'

/** A candlestick-marker group derived client-side: every marker sharing one
 * (pattern type, direction). Not a wire type — recomputed on each sweep. */
export interface CandlestickPatternGroup {
  /** Grouping key `${pattern}|${kind}` (from `candleGroupKey`). */
  key: string
  pattern: string | null
  kind: MarkerKind
  /** Instance count in the current sweep. */
  count: number
  /** Newest `event_ts` in the group — picks the default-on group after a sweep. */
  latestTs: string
}

/** The per-group legend-row / enabled-set layer id for a group key. */
export function candleGroupLayerId(key: string): string {
  return `${CANDLE_LAYER_ID}:${key}`
}

/** Reverse of `candleGroupLayerId`: the group key a legend-row id carries, or
 * `null` when the id isn't a candlestick group row (so the master + every other
 * layer route to the opt-out `hidden` set instead). */
export function candleGroupKeyFromLayerId(id: string): string | null {
  const prefix = `${CANDLE_LAYER_ID}:`
  return id.startsWith(prefix) ? id.slice(prefix.length) : null
}

/** Human display names for the 14 candlestick patterns the detector emits
 * (mirror of `PatternHit.pattern` in `analysis/patterns.py`). Keyed by the wire
 * value; an unknown token humanises (`some_new` → `Some new`) so a future
 * detector pattern still reads, and `null` (agent highlight, no pattern) → the
 * generic "Pattern". */
export const PATTERN_DISPLAY_NAMES: Record<string, string> = {
  doji: 'Doji',
  hammer: 'Hammer',
  hanging_man: 'Hanging man',
  marubozu: 'Marubozu',
  bullish_engulfing: 'Bullish engulfing',
  bearish_engulfing: 'Bearish engulfing',
  dark_cloud_cover: 'Dark cloud cover',
  piercing_line: 'Piercing line',
  bullish_harami: 'Bullish harami',
  bearish_harami: 'Bearish harami',
  morning_star: 'Morning star',
  evening_star: 'Evening star',
  three_white_soldiers: 'Three white soldiers',
  three_black_crows: 'Three black crows',
}

function humanize(token: string): string {
  const spaced = token.replaceAll('_', ' ').trim()
  return spaced === '' ? 'Pattern' : spaced[0].toUpperCase() + spaced.slice(1)
}

/** Display name for a candlestick pattern type. */
export function candlePatternDisplayName(pattern: string | null | undefined): string {
  if (pattern == null) return 'Pattern'
  return PATTERN_DISPLAY_NAMES[pattern] ?? humanize(pattern)
}

/** Direction word for a marker kind — the legend row's parenthetical, mirroring
 * the trendline legend's state word (`confirmed`/`forming`). */
export function candleDirectionLabel(kind: MarkerKind): string {
  if (kind === 'bullish_marker') return 'bullish'
  if (kind === 'bearish_marker') return 'bearish'
  return 'neutral'
}

/** Full legend label for a group, e.g. `Bullish engulfing (bullish)`. */
export function candleGroupLabel(group: CandlestickPatternGroup): string {
  return `${candlePatternDisplayName(group.pattern)} (${candleDirectionLabel(group.kind)})`
}

/**
 * Group markers by (pattern type, direction), preserving first-seen order and
 * counting instances. `latestTs` is the newest `event_ts` in the group. Pure and
 * order-preserving (the legend lists groups in first-seen order).
 */
export function groupCandlestickMarkers(
  markers: readonly ChartMarker[],
): CandlestickPatternGroup[] {
  const groups = new Map<string, CandlestickPatternGroup>()
  for (const m of markers) {
    const key = candleGroupKey(m)
    const existing = groups.get(key)
    if (existing) {
      existing.count += 1
      if (m.event_ts > existing.latestTs) existing.latestTs = m.event_ts
    } else {
      groups.set(key, {
        key,
        pattern: m.pattern ?? null,
        kind: m.kind,
        count: 1,
        latestTs: m.event_ts,
      })
    }
  }
  return [...groups.values()]
}

/**
 * The key of the group holding the newest marker — the single group enabled by
 * default after a sweep (Plan 0071 phase 2), so the chart is populated yet not
 * walled. `null` for an empty group list. Ties resolve to the first-seen group
 * (a stable, deterministic pick; ISO-8601 UTC timestamps compare chronologically
 * as strings). */
export function mostRecentGroupKey(groups: readonly CandlestickPatternGroup[]): string | null {
  let best: CandlestickPatternGroup | null = null
  for (const g of groups) {
    if (best === null || g.latestTs > best.latestTs) best = g
  }
  return best === null ? null : best.key
}
