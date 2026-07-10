/**
 * Pure layers-legend builder (Plan 0047 phase 9 / Plan 0067 phase 3 / Plan 0071
 * phase 2 — lifted verbatim out of `CandlestickChart`'s legend effect in the Plan
 * 0072 phase 8 decomposition, no behaviour change).
 *
 * Given the already-resolved colours + inputs, returns the ordered `ChartLayer[]`
 * the panel renders: one row per indicator overlay, a candlestick master + one
 * detail row per (pattern type, direction) group, one row per price line, and one
 * row per (pattern type, state) trendline group. Each row's colour equals the
 * colour the layer is drawn with. Pure — the hook resolves the DOM tokens and
 * calls this.
 */
import { overlayStyleColor, type ChartColors } from './chartSeries'
import type { ResolvedChartStyle } from './chartStyle'
import {
  CANDLE_MASTER_ID,
  CANDLE_MASTER_LABEL,
  candleGroupLabel,
  candleGroupLayerId,
  type CandlestickPatternGroup,
} from './candleGroups'
import { isSupportedOverlay, overlayLayerId } from './overlays'
import { priceLineColor, priceLineId, priceLineLabel } from './priceLines'
import { overlayLabel } from './tooltip'
import {
  patternDisplayName,
  patternStateKey,
  readTrendlineColors,
  trendlineColor,
  trendlineGroupLayerId,
  trendlineStateLabel,
} from './trendlines'
import type { ChartLayer } from '../components/LayersPanel'
import type { OverlaySpec, TrendlineSpec } from '../types/events'

export interface BuildChartLayersParams {
  overlays: ReadonlyArray<OverlaySpec> | undefined
  candleGroups: CandlestickPatternGroup[]
  enabledCandleGroups: ReadonlySet<string>
  visibleTrendlines: ReadonlyArray<TrendlineSpec>
  hidden: ReadonlySet<string>
  style: ResolvedChartStyle
  colors: ChartColors
  trendlineColors: ReturnType<typeof readTrendlineColors>
}

export function buildChartLayers({
  overlays,
  candleGroups,
  enabledCandleGroups,
  visibleTrendlines,
  hidden,
  style,
  colors,
  trendlineColors,
}: BuildChartLayersParams): ChartLayer[] {
  const next: ChartLayer[] = []
  for (const spec of overlays ?? []) {
    if (spec.kind === 'price_line' || !isSupportedOverlay(spec.kind)) continue
    const id = overlayLayerId(spec)
    next.push({
      id,
      label: overlayLabel(spec),
      color: overlayStyleColor(spec, style),
      kind: 'overlay',
      visible: !hidden.has(id),
      // The overlay kind keys the glossary tooltip (Plan 0065) — ema/sma/
      // supertrend resolve; a future unsupported kind degrades to plain text.
      glossaryKey: spec.kind,
    })
  }
  // Candlestick marker layer (Plan 0071 phase 2): a single MASTER row for the
  // whole layer, then one DETAIL row per (pattern type, direction) group with
  // its instance count, per-group visibility, and a highlight key. Replaces the
  // coarse per-direction marker rows + the standalone span row (both fold into
  // the master). Rows list even when a group is toggled off (so it re-enables).
  if (candleGroups.length > 0) {
    next.push({
      id: CANDLE_MASTER_ID,
      label: CANDLE_MASTER_LABEL,
      color: colors.markerNeutral,
      kind: 'marker',
      visible: !hidden.has(CANDLE_MASTER_ID),
    })
    for (const group of candleGroups) {
      const groupColor =
        group.kind === 'bullish_marker'
          ? colors.markerBullish
          : group.kind === 'bearish_marker'
            ? colors.markerBearish
            : colors.markerNeutral
      next.push({
        id: candleGroupLayerId(group.key),
        label: candleGroupLabel(group),
        color: groupColor,
        kind: 'marker',
        visible: enabledCandleGroups.has(group.key),
        count: group.count,
        highlightKey: group.key,
      })
    }
  }
  for (const spec of overlays ?? []) {
    if (spec.kind !== 'price_line') continue
    const id = priceLineId(spec)
    next.push({
      id,
      label: priceLineLabel(spec),
      color: priceLineColor(spec, colors),
      kind: 'price_line',
      visible: !hidden.has(id),
    })
  }
  // Grouped trendline rows (Plan 0067 phase 3 / ADR-0061): one row per (pattern
  // type, state) present, each with its type-colour swatch, instance count,
  // per-group visibility, and a highlight key for hover. Built from the deduped
  // set so a hidden group still lists (to re-enable).
  const groups = new Map<
    string,
    { pattern: string | null | undefined; style: TrendlineSpec['style']; count: number }
  >()
  for (const spec of visibleTrendlines) {
    const key = patternStateKey(spec)
    const existing = groups.get(key)
    if (existing) existing.count += 1
    else groups.set(key, { pattern: spec.pattern, style: spec.style, count: 1 })
  }
  for (const [key, group] of groups) {
    const id = trendlineGroupLayerId(key)
    next.push({
      id,
      label: `${patternDisplayName(group.pattern)} (${trendlineStateLabel(group.style)})`,
      color: trendlineColor(group.pattern, trendlineColors),
      kind: 'trendline',
      visible: !hidden.has(id),
      count: group.count,
      highlightKey: key,
    })
  }
  return next
}
