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

import type { Annotation } from '../types/sidecar/annotation'
import type { OverlaySpec } from '../types/events'

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

/** Human label for an overlay series, e.g. `EMA(20)`, `SMA(50)`, `PRICE_LINE`. */
export function overlayLabel(spec: OverlaySpec): string {
  const kind = spec.kind.toUpperCase()
  return spec.period != null ? `${kind}(${spec.period})` : kind
}

function markerKindLabel(kind: Annotation['kind']): string {
  return kind === 'bullish_marker' ? 'Bullish' : 'Bearish'
}

/**
 * Build the tooltip content for a crosshair at `time`, or `null` when there is
 * nothing to show (no time = pointer left the chart; or a bar with no marker
 * and no overlay reading). Annotations whose bar matches `time` contribute their
 * label; `overlays` are the readings the chart already pulled from `seriesData`.
 */
export function tooltipAtTime(
  time: UTCTimestamp | undefined,
  annotations: Annotation[],
  overlays: OverlayReading[],
): TooltipContent | null {
  if (time === undefined) return null
  const markers = annotations
    .filter((a) => Math.floor(new Date(a.event_ts).getTime() / 1000) === time)
    .map((a) => a.label?.trim() || markerKindLabel(a.kind))
  if (markers.length === 0 && overlays.length === 0) return null
  return { markers, overlays }
}
