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
      if (a.kind === 'bullish_marker') {
        return {
          time,
          position: 'belowBar' as const,
          shape: 'arrowUp' as const,
          color: colors.bullish,
          text,
        }
      }
      return {
        time,
        position: 'aboveBar' as const,
        shape: 'arrowDown' as const,
        color: colors.bearish,
        text,
      }
    })
    .sort((a, b) => (a.time as number) - (b.time as number))
}

function truncateLabel(label: string): string {
  return label.length <= MARKER_LABEL_MAX ? label : `${label.slice(0, MARKER_LABEL_MAX - 1)}…`
}
