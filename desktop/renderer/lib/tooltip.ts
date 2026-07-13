/**
 * Crosshair → tooltip content mapping (Plan 0047 phase 8). Pure: no React, no
 * chart instance. Given the crosshair time plus the data already in renderer
 * state (annotations + the overlay line readings the chart reads off
 * `seriesData`), it returns what the hover tooltip should show — a pattern
 * marker's label when hovering its bar, and each overlay line's name + value.
 *
 * No sidecar call: every input is already in the renderer (annotations are
 * polled/streamed elsewhere; overlay values come from the series the chart
 * already drew).
 */
import type { UTCTimestamp } from 'lightweight-charts'

import type { Divergence, MarkerKind, OverlaySpec, TrendlineSpec } from '../types/events'
import type { ChartMarker } from './markers'
import { candlePatternDisplayName } from './candleGroups'
import { patternDisplayName, trendlineStateLabel } from './trendlines'
import { divergenceGlossaryKey, divergenceLabel } from './divergences'
import { localize, term } from '../glossary/types'
import type { Locale } from './i18n'

export interface OverlayReading {
  label: string
  value: number
}

export interface TooltipContent {
  /** Pattern-marker read-outs on the hovered bar: the candlestick pattern's
   * display name (`Bullish engulfing`, `Doji`) when the marker carries one,
   * else its free-text `label`, else a direction word (Plan 0071 follow-up). */
  markers: string[]
  /** The what-it-means line for a SINGLE hovered candlestick marker whose
   * pattern token resolves in the glossary (Plan 0085). Absent when zero or
   * several markers coincide on the bar, or the token has no entry — the tooltip
   * then shows names only, so the meaning never overflows a stacked read-out. */
  markerMeaning?: string
  /** Each hovered overlay line's name + value at the crosshair. */
  overlays: OverlayReading[]
  /** Hovered trendline read-outs — pattern + state (Plan 0067 phase 2 /
   * ADR-0061). Absent when the cursor isn't over a line. */
  trendlines?: string[]
  /** Hovered divergence read-outs — kind name + glossary meaning (Plan 0091
   * phase 9 / ADR-0090). Absent when the cursor isn't over a divergence line. */
  divergences?: string[]
}

/**
 * Read-out for a hovered trendline: pattern name + state, e.g. "Rising wedge —
 * confirmed" (Plan 0067 phase 2 / ADR-0061). State comes from `style`
 * (solid=confirmed, dashed=forming); an unknown/absent pattern reads
 * "Trendline". Pattern + state only — role stays in the data (ADR-0061). Shares
 * the display-name + state helpers with the grouped legend so they read alike.
 */
export function trendlineTooltipText(spec: TrendlineSpec): string {
  return `${patternDisplayName(spec.pattern)} — ${trendlineStateLabel(spec.style)}`
}

/** Hovered-divergence read-out: the localized kind name plus its glossary
 * what-it-means line (Plan 0091 phase 9). Degrades to the bare kind name if the
 * glossary has no entry for the kind — a name is always shown, never a raw key. */
export function divergenceTooltipText(divergence: Divergence, locale: Locale = 'en'): string {
  const record = term(divergenceGlossaryKey(divergence.kind))
  const name = record ? localize(record.term, locale) : divergenceLabel(divergence.kind)
  const meaning = record ? localize(record.whatItMeans, locale) : ''
  return meaning ? `${name} — ${meaning}` : name
}

/** Default gap (px) between the crosshair and the tooltip box. */
export const TOOLTIP_OFFSET = 12

export interface TooltipBox {
  /** Crosshair point within the chart container, px. */
  x: number
  y: number
  /** The tooltip's own rendered size, px. */
  width: number
  height: number
  /** The chart container's size, px. */
  containerWidth: number
  containerHeight: number
}

/**
 * Edge-aware placement for the hover tooltip (Plan 0049 phase 13). Prefers down-
 * right of the crosshair, but FLIPS to the left / up when that would overflow the
 * container's right / bottom edge, then clamps so the box stays fully inside
 * (`left + width <= containerWidth`, `left >= 0`; likewise vertically). Pure, so
 * the flip logic is unit-tested without a DOM.
 */
export function tooltipPosition(
  box: TooltipBox,
  offset = TOOLTIP_OFFSET,
): {
  left: number
  top: number
} {
  const { x, y, width, height, containerWidth, containerHeight } = box
  let left = x + offset + width > containerWidth ? x - offset - width : x + offset
  let top = y + offset + height > containerHeight ? y - offset - height : y + offset
  left = Math.min(Math.max(left, 0), Math.max(0, containerWidth - width))
  top = Math.min(Math.max(top, 0), Math.max(0, containerHeight - height))
  return { left, top }
}

/** Human label for an overlay series, e.g. `EMA(20)`, `SMA(50)`, `PRICE_LINE`. */
export function overlayLabel(spec: OverlaySpec): string {
  const kind = spec.kind.toUpperCase()
  return spec.period != null ? `${kind}(${spec.period})` : kind
}

function markerKindLabel(kind: MarkerKind): string {
  if (kind === 'bullish_marker') return 'Bullish'
  if (kind === 'bearish_marker') return 'Bearish'
  return 'Neutral'
}

/**
 * Build the tooltip content for a crosshair at `time`, or `null` when there is
 * nothing to show (no time = pointer left the chart; or a bar with no marker
 * and no overlay reading). Annotations whose bar matches `time` contribute their
 * label; `overlays` are the readings the chart already pulled from `seriesData`.
 */
export function tooltipAtTime(
  time: UTCTimestamp | undefined,
  chartMarkers: ChartMarker[],
  overlays: OverlayReading[],
  locale: Locale = 'en',
): TooltipContent | null {
  if (time === undefined) return null
  const onBar = chartMarkers.filter(
    (m) => Math.floor(new Date(m.event_ts).getTime() / 1000) === time,
  )
  const markers = onBar.map((m) =>
    // A candlestick sweep marker names its pattern (ADR-0045); prefer that
    // display name over the free-text label or a bare direction word.
    m.pattern != null
      ? candlePatternDisplayName(m.pattern)
      : m.label?.trim() || markerKindLabel(m.kind),
  )
  if (markers.length === 0 && overlays.length === 0) return null
  // A single hovered candlestick marker discloses its glossary meaning (Plan
  // 0085); when several markers coincide the meaning lines would overflow, so
  // names-only. An unknown token (no glossary entry) also degrades to name-only.
  let markerMeaning: string | undefined
  if (onBar.length === 1 && onBar[0].pattern != null) {
    const record = term(onBar[0].pattern)
    if (record) markerMeaning = localize(record.whatItMeans, locale)
  }
  return { markers, overlays, markerMeaning }
}
