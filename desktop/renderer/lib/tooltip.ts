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

import type { MarkerKind, OverlaySpec } from '../types/events'
import type { ChartMarker } from './markers'

export interface OverlayReading {
  label: string
  value: number
}

export interface TooltipContent {
  /** Pattern-marker labels on the hovered bar (a marker's `label`, or a
   * direction word when it carries none). */
  markers: string[]
  /** Each hovered overlay line's name + value at the crosshair. */
  overlays: OverlayReading[]
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
): TooltipContent | null {
  if (time === undefined) return null
  const markers = chartMarkers
    .filter((m) => Math.floor(new Date(m.event_ts).getTime() / 1000) === time)
    .map((m) => m.label?.trim() || markerKindLabel(m.kind))
  if (markers.length === 0 && overlays.length === 0) return null
  return { markers, overlays }
}
