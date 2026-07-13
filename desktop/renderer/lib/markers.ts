/**
 * Annotation → chart-marker mapping (Plan 0029 phase 2, moved out of
 * `CandlestickChart.tsx`). Pure: no React, no chart instance — inputs are
 * `Annotation` records, outputs are lightweight-charts series markers the chart
 * layer hands to the v5 markers plugin (`createSeriesMarkers(series).setMarkers(...)`).
 */
import type { SeriesMarker, UTCTimestamp } from 'lightweight-charts'

import type { MarkerKind } from '../types/events'

const MARKER_LABEL_MAX = 24

/**
 * The renderer's unified marker model (Plan 0049 / ADR-0045). Both the polled
 * annotations (persisted, bull/bear only) and the live `chart.highlight` markers
 * (which can be `neutral_marker` and carry `pattern` / a bar span / `strength`)
 * are mapped into this one shape before the chart draws them, so the lossy
 * coercion-to-`Annotation` is gone and the span/strength/identity survive to the
 * chart layer. A point marker leaves `span_*` unset; a multi-bar pattern carries
 * both endpoints (phase 7 draws the span box from them).
 */
export interface ChartMarker {
  event_ts: string
  kind: MarkerKind
  label?: string | null
  pattern?: string | null
  span_start_ts?: string | null
  span_end_ts?: string | null
  strength?: number | null
}

/** Default marker colors — used when the caller passes none. These are the
 * light-theme values; the chart overrides them with theme tokens (Plan 0033
 * phase 4). Kept as the default so non-DOM unit tests stay color-stable. */
export const DEFAULT_MARKER_COLORS: MarkerColors = {
  bullish: '#16a34a',
  bearish: '#dc2626',
  neutral: '#64748b',
}

export interface MarkerColors {
  bullish: string
  bearish: string
  /** Neutral patterns (doji, neutral marubozu) — Plan 0049. */
  neutral: string
}

/** Layers-legend id for a marker direction group (Plan 0047 phase 9, extended
 * Plan 0049). Markers are grouped by direction, so all markers of one direction
 * share one toggle and one row. */
export function markerLayerId(kind: MarkerKind): string {
  if (kind === 'bullish_marker') return 'marker:bullish'
  if (kind === 'bearish_marker') return 'marker:bearish'
  return 'marker:neutral'
}

/** Human label for a marker direction group in the layers legend. */
export function markerLayerLabel(kind: MarkerKind): string {
  if (kind === 'bullish_marker') return 'Bullish markers'
  if (kind === 'bearish_marker') return 'Bearish markers'
  return 'Neutral markers'
}

/** The (pattern type, direction) grouping key of a candlestick marker (Plan 0071
 * phase 2). A marker with no `pattern` groups under `unknown|<kind>` so agent-
 * authored highlights still form a coherent row; direction (`kind`) keeps a
 * bullish and a bearish hit on the same bar in separate groups (the ADR-0045
 * same-bar dedup lesson). The grouped-legend + draw-on-select gates on this. */
export function candleGroupKey(m: ChartMarker): string {
  return `${m.pattern ?? 'unknown'}|${m.kind}`
}

// Marker glyph sizing (Plan 0047 phase 7). lightweight-charts' default marker
// `size` is 1; the old markers used it and read as too subtle. `NEUTRAL_SIZE` is
// the baseline for a marker with no known strength (the current live path — see
// below); a strength-scaled marker ranges `BASE_SIZE`..`STRONG_SIZE`.
const NEUTRAL_SIZE = 2.0
const BASE_SIZE = 1.6
const STRONG_SIZE = 3.0
// Weakest strength still renders at this alpha so a low-strength marker stays
// visible; strength 1.0 reaches full opacity.
const WEAK_ALPHA = 0.5

// Hover-highlight (Plan 0071 phase 2): when a legend group is hovered, its
// markers grow (emphasis) and every other drawn marker fades (dim) so the
// hovered group reads out of the field — the marker analogue of the trendline
// legend's emphasise/dim (ADR-0061).
const HIGHLIGHT_EMPHASIS_SCALE = 1.5
const HIGHLIGHT_DIM_ALPHA = 0.3

export interface MarkerVisual {
  /** lightweight-charts marker `size` multiplier. */
  size: number
  /** Resolved marker color (direction theme token, intensity-scaled by strength). */
  color: string
}

function clamp01(x: number): number {
  return x < 0 ? 0 : x > 1 ? 1 : x
}

/** Append an alpha byte to a `#rrggbb` hex color. Non-hex inputs (a named color
 * or `rgb(...)`) are returned unchanged — we can't safely modulate those, and the
 * theme tokens resolve to 6-digit hex (getComputedStyle returns the authored
 * custom-property value). */
function withAlpha(color: string, alpha: number): string {
  const match = /^#([0-9a-f]{6})$/i.exec(color.trim())
  if (!match) return color
  const byte = Math.round(clamp01(alpha) * 255)
    .toString(16)
    .padStart(2, '0')
  return `#${match[1]}${byte}`
}

/**
 * Resolve a marker's visual (size + color) from its direction and pattern
 * strength (Plan 0047 phase 7). Direction selects the bull/bear theme token
 * (never a hardcoded hex — the chart passes theme-resolved `colors`); strength
 * ∈ [0,1] scales both the glyph size and the colour intensity (alpha), so a
 * strong signal reads bigger and bolder than a weak one.
 *
 * `strength === null` is the *unknown* case — the current live path, because the
 * annotation/marker data model carries no strength field yet. It renders at a
 * full-intensity colour and a baseline size that is still clearly larger than
 * the old default. When a future data-layer plan adds a strength signal, callers
 * pass it through and the scaling lights up with no change here.
 */
export function markerVisual(
  kind: MarkerKind,
  strength: number | null,
  colors: MarkerColors = DEFAULT_MARKER_COLORS,
): MarkerVisual {
  const base =
    kind === 'bullish_marker'
      ? colors.bullish
      : kind === 'bearish_marker'
        ? colors.bearish
        : colors.neutral
  if (strength === null) {
    return { size: NEUTRAL_SIZE, color: base }
  }
  const s = clamp01(strength)
  const size = BASE_SIZE + s * (STRONG_SIZE - BASE_SIZE)
  const alpha = WEAK_ALPHA + s * (1 - WEAK_ALPHA)
  return { size, color: withAlpha(base, alpha) }
}

/**
 * Map chart markers to lightweight-charts series markers. Bullish goes below the
 * bar with an up-arrow; bearish goes above with a down-arrow; a neutral pattern
 * (doji) draws an in-bar circle so it reads as "no direction". Labels are
 * truncated to ~MARKER_LABEL_MAX chars so a runaway agent can't push a 5KB string
 * into the chart tooltip layer.
 *
 * `strength` (carried by live sweep markers, absent on polled annotations) scales
 * the glyph size + colour intensity via `markerVisual`; an absent strength is the
 * `null` baseline.
 *
 * `colors` lets the chart layer supply theme-resolved bull/bear/neutral colors;
 * omit it and the light-theme defaults are used (the unit test's expectation).
 *
 * Returned markers are sorted ascending by time — lightweight-charts
 * requires this and will throw on out-of-order markers.
 */
export function annotationsToMarkers(
  markers: ChartMarker[],
  colors: MarkerColors = DEFAULT_MARKER_COLORS,
  options: { includeText?: boolean; highlightGroupKey?: string | null } = {},
): SeriesMarker<UTCTimestamp>[] {
  const { includeText = true, highlightGroupKey = null } = options
  return markers
    .map((m) => {
      const time = Math.floor(new Date(m.event_ts).getTime() / 1000) as UTCTimestamp
      // Plan 0049 phase 12: the chart passes includeText:false so markers render
      // glyph-only — lightweight-charts marker text has no backing and reads as
      // bare overlapping text over the candles. The label instead shows in the
      // backed hover tooltip (Plan 0047). Other callers keep the text.
      const text = includeText && m.label ? truncateLabel(m.label) : ''
      let { size, color } = markerVisual(m.kind, m.strength ?? null, colors)
      // Hover-highlight (Plan 0071 phase 2): a hovered legend group emphasises its
      // markers and fades the rest. Null (no hover) leaves every marker as drawn.
      if (highlightGroupKey !== null) {
        if (candleGroupKey(m) === highlightGroupKey) size *= HIGHLIGHT_EMPHASIS_SCALE
        else color = withAlpha(color, HIGHLIGHT_DIM_ALPHA)
      }
      if (m.kind === 'bullish_marker') {
        return { time, position: 'belowBar' as const, shape: 'arrowUp' as const, color, size, text }
      }
      if (m.kind === 'bearish_marker') {
        return {
          time,
          position: 'aboveBar' as const,
          shape: 'arrowDown' as const,
          color,
          size,
          text,
        }
      }
      // Neutral (doji et al.): an in-bar circle — no up/down implication.
      return { time, position: 'inBar' as const, shape: 'circle' as const, color, size, text }
    })
    .sort((a, b) => (a.time as number) - (b.time as number))
}

function truncateLabel(label: string): string {
  return label.length <= MARKER_LABEL_MAX ? label : `${label.slice(0, MARKER_LABEL_MAX - 1)}…`
}
