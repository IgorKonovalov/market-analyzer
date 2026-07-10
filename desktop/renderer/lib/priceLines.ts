/**
 * Pure `price_line` overlay helpers (Plan 0047 phase 9 — lifted verbatim out of
 * `CandlestickChart` in the Plan 0072 phase 8 decomposition, no behaviour change).
 * Shared by `usePriceLines` (which draws them) and the layers-legend builder
 * (which lists them) so the legend swatch matches the drawn line.
 */
import type { ChartColors } from './chartSeries'
import type { OverlaySpec } from '../types/events'

/** Layers-legend id for a `price_line` overlay. */
export function priceLineId(spec: OverlaySpec): string {
  return `pline:${spec.label ?? spec.price ?? 'na'}`
}

/** Display label for a price line in the legend, e.g. `R1 (61335.75)`. */
export function priceLineLabel(spec: OverlaySpec): string {
  const name = spec.label ?? 'level'
  return spec.price != null ? `${name} (${spec.price})` : name
}

/** Price-line colour: a support level reads bullish, a resistance level bearish,
 * a roleless level uses the neutral clicked/accent token — so the legend swatch
 * matches the drawn line. */
export function priceLineColor(spec: OverlaySpec, colors: ChartColors): string {
  if (spec.role === 'support') return colors.markerBullish
  if (spec.role === 'resistance') return colors.markerBearish
  return colors.markerClicked
}
