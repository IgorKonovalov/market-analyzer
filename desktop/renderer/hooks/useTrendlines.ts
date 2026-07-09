/**
 * Plan 0052 phase 4 (ADR-0049): the trendline-overlay reconcile, lifted out of
 * `CandlestickChart` into a hook from the start — the chart is flagged
 * god-component debt (the 0047/0049 decomposition follow-up), so this lands as
 * a hook, not inline effects.
 *
 * FEEDS an already-attached `TrendlinePrimitive` its specs, theme-resolved
 * colours, and legend-row visibility on every change. It does NOT attach the
 * primitive: the primitive is created and `attachPrimitive`'d in the component's
 * chart-creation effect (mirroring the span band), so its lifecycle is tied to
 * the chart's — attached to the LIVE series, disposed by `chart.remove()`, and
 * re-created on any chart re-mount (incl. React StrictMode's dev double-invoke).
 *
 * This is deliberate: attaching inside the hook (guarded by a "once" ref with no
 * cleanup) stranded the primitive on a discarded chart under StrictMode / chart
 * re-creation — it computed segments against stale objects but the live chart
 * had no primitive to paint, so trendlines never drew (Plan 0064 follow-up). The
 * span band never had this bug because it is created in the chart-creation
 * effect; trendlines now follow the same pattern.
 *
 * MUST be called after the component's chart-creation effect so that, on mount,
 * `trendlinePrimitiveRef` is populated before this effect runs.
 */
import { useEffect } from 'react'
import type { RefObject } from 'react'

import type { EffectiveTheme } from '../lib/theme'
import { TrendlinePrimitive, readTrendlineColors } from '../lib/trendlines'
import type { TrendlineSpec } from '../types/events'

export interface UseTrendlinesParams {
  /** The trendline specs to draw — already deduped AND filtered to the groups
   * whose legend rows are checked (Plan 0067 phase 3): visibility is per-(pattern
   * type, state) group, so the component removes hidden groups before feeding
   * them here rather than toggling a single global flag. */
  trendlines: ReadonlyArray<TrendlineSpec>
  /** The hovered legend group's `patternStateKey`, or null — the primitive
   * emphasises its lines and dims the rest (Plan 0067 phase 3). */
  highlightKey: string | null
  /** Re-resolves the colour tokens off the DOM when the theme flips. */
  effectiveTheme: EffectiveTheme
  /** Changes when the chart (and thus the trendline primitive) is rebuilt (Plan
   * 0068 phase 4: a candle-type switch). The creation effect attaches a FRESH,
   * empty primitive on rebuild; `trendlinePrimitiveRef` is a stable object, so
   * this token is what re-runs the feed so the new primitive gets the specs. */
  rebuildToken?: unknown
}

export function useTrendlines(
  containerRef: RefObject<HTMLDivElement>,
  trendlinePrimitiveRef: RefObject<TrendlinePrimitive>,
  { trendlines, highlightKey, effectiveTheme, rebuildToken }: UseTrendlinesParams,
): void {
  useEffect(() => {
    const primitive = trendlinePrimitiveRef.current
    const container = containerRef.current
    if (!primitive || !container) return
    // Recolours in place on a theme flip (`effectiveTheme` in the deps re-runs
    // the token read); the primitive persists — no remount, no re-attach. On a
    // rebuild (`rebuildToken`) it re-feeds the freshly-attached primitive.
    primitive.setColors(readTrendlineColors(container))
    primitive.setTrendlines(trendlines)
    primitive.setHighlightedGroup(highlightKey)
  }, [containerRef, trendlinePrimitiveRef, trendlines, highlightKey, effectiveTheme, rebuildToken])
}
