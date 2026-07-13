/**
 * Live-value builder for the inline chart legend (Plan 0096 phase 2).
 *
 * Given the bars the chart holds and the merged overlay set, returns a map of
 * legend layer id → the layer's latest (last-bar) value, formatted like the
 * hover tooltip (`toFixed(2)`). Pure: it recomputes each indicator client-side
 * via the same `computeOverlayData` / `computeObv` the chart draws with, so the
 * legend reads exactly what's on screen — no series-ref access, no sidecar call.
 * Only series-backed rows (indicator overlays + the always-on OBV strip) get a
 * value; markers, price lines and trendlines carry a count instead.
 */
import type { LineData } from 'lightweight-charts'

import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'
import { OBV_LAYER_ID } from './chartSeries'
import { computeOverlayData, isSupportedOverlay, overlayLayerId } from './overlays'
import { computeObv } from './volume'

/** The last finite `.value` in a series line (skipping trailing whitespace / gaps). */
function lastValue(points: ReadonlyArray<LineData | { value?: number }>): number | undefined {
  for (let i = points.length - 1; i >= 0; i--) {
    const v = (points[i] as LineData).value
    if (typeof v === 'number' && Number.isFinite(v)) return v
  }
  return undefined
}

/**
 * Map each series-backed legend row to its latest formatted value.
 * `overlays` is the merged (agent ⊕ user) set; `hasObv` mirrors `bars.length > 0`.
 */
export function buildLegendValues(
  bars: Bar[],
  overlays: ReadonlyArray<OverlaySpec> | undefined,
  hasObv: boolean,
): Map<string, string> {
  const values = new Map<string, string>()
  if (bars.length === 0) return values
  for (const spec of overlays ?? []) {
    if (spec.kind === 'price_line' || !isSupportedOverlay(spec.kind)) continue
    const v = lastValue(computeOverlayData(bars, spec))
    if (v !== undefined) values.set(overlayLayerId(spec), v.toFixed(2))
  }
  if (hasObv) {
    const v = lastValue(computeObv(bars))
    if (v !== undefined) values.set(OBV_LAYER_ID, v.toFixed(2))
  }
  return values
}
