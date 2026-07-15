/**
 * legendRouting — the pure decision glue joining the chart's two legend systems
 * (Plan 0098 phase 4, ADR-0092). The layers legend carries both candlestick-pattern
 * GROUP rows (opt-in, routed to the enabled set) and everything else (overlays /
 * candlestick master / price lines / trendline groups, opt-out, routed to the hidden
 * set); hover-highlight likewise splits between marker emphasis and the trendline
 * primitive. Extracted from CandlestickChart's inline `onLayerToggle` /
 * `onLayerHighlight` callbacks so the branch is a pure, unit-tested function — the
 * component just dispatches on the route. No React.
 */
import { candleGroupKeyFromLayerId } from '../candleGroups'

export type ToggleRoute = { kind: 'candleGroup'; groupKey: string } | { kind: 'layer'; id: string }

/** A candlestick GROUP row id routes to a group toggle; every other id to a hidden
 * (layer visibility) toggle. */
export function routeLayerToggle(id: string): ToggleRoute {
  const groupKey = candleGroupKeyFromLayerId(id)
  return groupKey !== null ? { kind: 'candleGroup', groupKey } : { kind: 'layer', id }
}

export type HighlightRoute =
  | { kind: 'candleGroup'; key: string }
  | { kind: 'trendline'; key: string | null }

/** A key that names a candlestick group routes to marker emphasis; any other key (or
 * null, on hover-out) routes to the trendline primitive. */
export function routeLayerHighlight(
  key: string | null,
  candleKeySet: ReadonlySet<string>,
): HighlightRoute {
  if (key !== null && candleKeySet.has(key)) return { kind: 'candleGroup', key }
  return { kind: 'trendline', key }
}
