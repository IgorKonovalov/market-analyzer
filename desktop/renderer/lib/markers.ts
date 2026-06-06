/**
 * Annotation → chart-marker mapping (Plan 0029 phase 2, moved out of
 * `CandlestickChart.tsx`). Pure: no React, no chart instance — inputs are
 * `Annotation` records, outputs are lightweight-charts series markers the chart
 * layer hands to `series.setMarkers(...)`.
 */
import type { SeriesMarker, UTCTimestamp } from 'lightweight-charts'

import type { Annotation } from '../types/sidecar/annotation'

const MARKER_LABEL_MAX = 24

/** Default marker colors — used when the caller passes none. These are the
 * light-theme values; the chart overrides them with theme tokens (Plan 0033
 * phase 4). Kept as the default so non-DOM unit tests stay color-stable. */
export const DEFAULT_MARKER_COLORS: MarkerColors = {
  bullish: '#16a34a',
  bearish: '#dc2626',
}

export interface MarkerColors {
  bullish: string
  bearish: string
}

type MarkerDirection = Annotation['kind']

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
  direction: MarkerDirection,
  strength: number | null,
  colors: MarkerColors = DEFAULT_MARKER_COLORS,
): MarkerVisual {
  const base = direction === 'bullish_marker' ? colors.bullish : colors.bearish
  if (strength === null) {
    return { size: NEUTRAL_SIZE, color: base }
  }
  const s = clamp01(strength)
  const size = BASE_SIZE + s * (STRONG_SIZE - BASE_SIZE)
  const alpha = WEAK_ALPHA + s * (1 - WEAK_ALPHA)
  return { size, color: withAlpha(base, alpha) }
}

/**
 * Map annotations to lightweight-charts series markers. Bullish goes
 * below the bar with an up-arrow; bearish goes above with a down-arrow.
 * Labels are truncated to ~MARKER_LABEL_MAX chars so a runaway agent
 * can't push a 5KB string into the chart tooltip layer.
 *
 * `colors` lets the chart layer supply theme-resolved bull/bear colors; omit it
 * and the light-theme defaults are used (the unit test's expectation).
 *
 * Returned markers are sorted ascending by time — lightweight-charts
 * requires this and will throw on out-of-order markers.
 */
export function annotationsToMarkers(
  annotations: Annotation[],
  colors: MarkerColors = DEFAULT_MARKER_COLORS,
): SeriesMarker<UTCTimestamp>[] {
  return annotations
    .map((a) => {
      const time = Math.floor(new Date(a.event_ts).getTime() / 1000) as UTCTimestamp
      const text = a.label ? truncateLabel(a.label) : ''
      // Annotations carry no strength field (the sidecar model has only
      // kind/label), so the live path is the `null`-strength baseline: a larger
      // glyph in the full-intensity direction token. `markerVisual` is ready to
      // scale once a strength signal exists end-to-end.
      const { size, color } = markerVisual(a.kind, null, colors)
      if (a.kind === 'bullish_marker') {
        return {
          time,
          position: 'belowBar' as const,
          shape: 'arrowUp' as const,
          color,
          size,
          text,
        }
      }
      return {
        time,
        position: 'aboveBar' as const,
        shape: 'arrowDown' as const,
        color,
        size,
        text,
      }
    })
    .sort((a, b) => (a.time as number) - (b.time as number))
}

function truncateLabel(label: string): string {
  return label.length <= MARKER_LABEL_MAX ? label : `${label.slice(0, MARKER_LABEL_MAX - 1)}…`
}
