/**
 * Overlay registry (Plan 0029 phase 2). One table maps each supported overlay
 * `kind` to its line color and its indicator math, collapsing what used to be
 * four scattered edit sites in `CandlestickChart.tsx` (a supported-kinds set, a
 * color switch, a compute switch, and the reconcile loop) into a single entry.
 *
 * Adding a new overlay kind is now one registry row — the supported-kinds check
 * (`isSupportedOverlay`), the color (`overlayColorFor`), and the data
 * (`computeOverlayData`) all read from here. The chart's reconcile loop stays in
 * the component but consults these helpers.
 *
 * MVP scope is `ema` and `sma`. `rsi`/`macd`/`bbands` are reserved `OverlayKind`
 * values in the typed envelope schema but have no registry entry yet, so the
 * chart logs-and-skips them (see `isSupportedOverlay`).
 */
import type { LineData } from 'lightweight-charts'

import { computeEma, computeSma } from './indicators'
import type { Bar } from '../types/sidecar/bar'
import type { OverlayKind, OverlaySpec } from '../types/events'

export interface OverlayDefinition {
  /** Line color for the overlay series. */
  color: string
  /** Trailing indicator math: value at index `i` uses `bars[0..=i]` only. */
  compute(bars: Bar[], period: number): LineData[]
}

/** The single source of truth for supported overlays. `Partial` because the
 * `OverlayKind` union also carries MVP-unsupported kinds (rsi/macd/bbands) that
 * deliberately have no entry yet. */
export const OVERLAY_REGISTRY: Partial<Record<OverlayKind, OverlayDefinition>> = {
  ema: { color: '#2563eb', compute: (bars, period) => computeEma(bars, period) },
  sma: { color: '#f97316', compute: (bars, period) => computeSma(bars, period) },
}

const FALLBACK_COLOR = '#888888'

/** Whether an overlay kind is renderable (has a registry entry). Read at call
 * time so a registry mutation (e.g. in a test) is reflected immediately. */
export function isSupportedOverlay(kind: OverlayKind): boolean {
  return kind in OVERLAY_REGISTRY
}

/** Line color for an overlay, falling back to neutral grey for an
 * unregistered kind (which the reconcile loop never actually draws). */
export function overlayColorFor(spec: OverlaySpec): string {
  return OVERLAY_REGISTRY[spec.kind]?.color ?? FALLBACK_COLOR
}

/** Compute the overlay's line data, or `[]` for an unregistered kind or a
 * missing period. The underlying indicator returns `[]` for too-short input. */
export function computeOverlayData(bars: Bar[], spec: OverlaySpec): LineData[] {
  const definition = OVERLAY_REGISTRY[spec.kind]
  if (definition === undefined) return []
  if (spec.period === null || spec.period === undefined) return []
  return definition.compute(bars, spec.period)
}
