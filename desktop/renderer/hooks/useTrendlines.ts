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
import { TRENDLINE_LAYER_ID, TrendlinePrimitive, readTrendlineColors } from '../lib/trendlines'
import type { TrendlineSpec } from '../types/events'

export interface UseTrendlinesParams {
  /** The trendline specs to draw (from the `chart.trendlines` event). */
  trendlines: ReadonlyArray<TrendlineSpec>
  /** The chart's hidden-layer id set; this hook reads `TRENDLINE_LAYER_ID`. */
  hidden: ReadonlySet<string>
  /** Re-resolves the colour tokens off the DOM when the theme flips. */
  effectiveTheme: EffectiveTheme
}

export function useTrendlines(
  containerRef: RefObject<HTMLDivElement>,
  trendlinePrimitiveRef: RefObject<TrendlinePrimitive>,
  { trendlines, hidden, effectiveTheme }: UseTrendlinesParams,
): void {
  useEffect(() => {
    const primitive = trendlinePrimitiveRef.current
    const container = containerRef.current
    if (!primitive || !container) return
    // Recolours in place on a theme flip (`effectiveTheme` in the deps re-runs
    // the token read); the primitive persists — no remount, no re-attach.
    primitive.setColors(readTrendlineColors(container))
    primitive.setTrendlines(trendlines)
    primitive.setVisible(!hidden.has(TRENDLINE_LAYER_ID))
  }, [containerRef, trendlinePrimitiveRef, trendlines, hidden, effectiveTheme])
}
