/**
 * Annotation → chart-marker mapping (Plan 0029 phase 2, moved out of
 * `CandlestickChart.tsx`). Pure: no React, no chart instance — inputs are
 * `Annotation` records, outputs are lightweight-charts series markers the chart
 * layer hands to `series.setMarkers(...)`.
 */
import type { SeriesMarker, UTCTimestamp } from 'lightweight-charts'

import type { Annotation } from '../types/sidecar/annotation'

const MARKER_LABEL_MAX = 24
const BULLISH_COLOR = '#16a34a'
const BEARISH_COLOR = '#dc2626'

/**
 * Map annotations to lightweight-charts series markers. Bullish goes
 * below the bar with an up-arrow; bearish goes above with a down-arrow.
 * Labels are truncated to ~MARKER_LABEL_MAX chars so a runaway agent
 * can't push a 5KB string into the chart tooltip layer.
 *
 * Returned markers are sorted ascending by time — lightweight-charts
 * requires this and will throw on out-of-order markers.
 */
export function annotationsToMarkers(annotations: Annotation[]): SeriesMarker<UTCTimestamp>[] {
  return annotations
    .map((a) => {
      const time = Math.floor(new Date(a.event_ts).getTime() / 1000) as UTCTimestamp
      const text = a.label ? truncateLabel(a.label) : ''
      if (a.kind === 'bullish_marker') {
        return {
          time,
          position: 'belowBar' as const,
          shape: 'arrowUp' as const,
          color: BULLISH_COLOR,
          text,
        }
      }
      return {
        time,
        position: 'aboveBar' as const,
        shape: 'arrowDown' as const,
        color: BEARISH_COLOR,
        text,
      }
    })
    .sort((a, b) => (a.time as number) - (b.time as number))
}

function truncateLabel(label: string): string {
  return label.length <= MARKER_LABEL_MAX ? label : `${label.slice(0, MARKER_LABEL_MAX - 1)}…`
}
