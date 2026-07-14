/**
 * OBV pane lifecycle reconcile (Plan 0105 phase 3).
 *
 * OBV used to be created ONCE in the chart-creation effect and only ever hidden
 * via `applyOptions({ visible: false })` — but v5 has no pane-hide and enforces
 * a ~30px pane minimum, so a toggled-off OBV (and a Clean chart, where OBV is
 * off by default — ADR-0089) still spent vertical space on an empty pane. This
 * hook reconciles the OBV pane the way `useOscillatorPanes` reconciles the
 * oscillator panes: lazy-create the pane + series + divergence primitive when
 * wanted, remove them when not, keeping OBV the FIRST managed sub-pane — the
 * pane is registered at slot 0 (`PaneRegistry.ensure(id, 0)`), so oscillators
 * stay 2..N even when OBV is re-enabled after they exist.
 *
 * Wanted = the OBV legend row is visible OR an obv divergence needs the pane
 * (the same required-pane rule `useOscillatorPanes` applies via `requiredKinds`:
 * a divergence's oscillator segment must have a pane to draw on even when the
 * user toggled the series off — in that case the pane exists, the OBV line is
 * hidden, and only the divergence primitive draws).
 *
 * MUST be called after the chart-creation effect (the chart + registry refs
 * exist) and before `useDivergences` (which feeds the primitive attached here).
 * `rebuildToken` (candleType) re-runs it after a chart rebuild — the creation
 * effect's cleanup nulls the shared refs, so the fresh chart re-creates.
 */
import { useEffect } from 'react'
import type { MutableRefObject, RefObject } from 'react'
import { LineSeries } from 'lightweight-charts'
import type { IChartApi, ISeriesApi, LineWidth } from 'lightweight-charts'

import {
  OBV_LAYER_ID,
  OBV_PANE_HEIGHT,
  OBV_PANE_ID,
  OBV_SCALE_ID,
  chartColorsFrom,
} from '../lib/chartSeries'
import { resolveChartStyle } from '../lib/chartStyle'
import { DivergencePrimitive, readDivergenceColors } from '../lib/divergences'
import { PaneLabelPrimitive, paneLabelFor } from '../lib/paneLabel'
import { computeObv } from '../lib/volume'
import type { PaneRegistry } from '../lib/panes'
import type { EffectiveTheme } from '../lib/theme'
import type { Bar } from '../types/sidecar/bar'
import type { Divergence } from '../types/events'

/** The managed-order slot OBV claims — the first sub-pane below price, ahead of
 * every oscillator pane (the Plan 0095/0091 pane-order invariant, kept under
 * lazy re-creation). */
const OBV_PANE_SLOT = 0

export interface UseObvPaneParams {
  bars: Bar[]
  hidden: ReadonlySet<string>
  /** The current divergence set — an `obv` divergence keeps the pane alive (its
   * oscillator-pivot segment draws there) even when the OBV row is toggled off. */
  divergences: ReadonlyArray<Divergence>
  effectiveThemeRef: RefObject<EffectiveTheme>
  rebuildToken: unknown
  syncTestRenderHook: () => void
}

export function useObvPane(
  chartRef: RefObject<IChartApi | null>,
  containerRef: RefObject<HTMLDivElement | null>,
  paneRegistryRef: RefObject<PaneRegistry | null>,
  obvSeriesRef: MutableRefObject<ISeriesApi<'Line'> | null>,
  obvDivergencePrimitiveRef: MutableRefObject<DivergencePrimitive | null>,
  {
    bars,
    hidden,
    divergences,
    effectiveThemeRef,
    rebuildToken,
    syncTestRenderHook,
  }: UseObvPaneParams,
): void {
  useEffect(() => {
    const chart = chartRef.current
    const registry = paneRegistryRef.current
    const container = containerRef.current
    const theme = effectiveThemeRef.current
    if (!chart || !registry || !container || !theme) return

    const visible = !hidden.has(OBV_LAYER_ID)
    const divergenceNeedsPane = divergences.some((d) => d.oscillator === 'obv')
    const wanted = visible || divergenceNeedsPane

    if (!wanted) {
      const series = obvSeriesRef.current
      if (series !== null) {
        chart.removeSeries(series)
        registry.remove(OBV_PANE_ID)
        obvSeriesRef.current = null
        obvDivergencePrimitiveRef.current = null
        syncTestRenderHook()
      }
      return
    }

    let series = obvSeriesRef.current
    if (series === null) {
      const style = resolveChartStyle(container, theme)
      const colors = chartColorsFrom(style)
      const paneIndex = registry.ensure(OBV_PANE_ID, OBV_PANE_SLOT)
      series = chart.addSeries(
        LineSeries,
        {
          // OBV's own (per-pane) overlay scale — keeps it a distinguishable
          // derived series, not an agent overlay.
          priceScaleId: OBV_SCALE_ID,
          color: colors.obv,
          lineWidth: style.widths.obv as LineWidth,
          priceLineVisible: false,
          lastValueVisible: false,
        },
        paneIndex,
      )
      registry.pane(OBV_PANE_ID)?.setHeight(OBV_PANE_HEIGHT)
      // The pane's divergence primitive rides the OBV series (disposed with it
      // on removal / chart.remove) and is re-attached on every (re)create;
      // `useDivergences` feeds it the obv oscillator-pivot segments.
      const primitive = new DivergencePrimitive('oscillator', readDivergenceColors(container))
      series.attachPrimitive(primitive)
      // The pane's name label (Plan 0105 phase 4) — rides the series so it
      // survives pan/zoom and dies with the pane.
      series.attachPrimitive(new PaneLabelPrimitive(paneLabelFor('obv')))
      obvSeriesRef.current = series
      obvDivergencePrimitiveRef.current = primitive
    }
    series.setData(computeObv(bars))
    // Divergence-only mode: the pane stays (the primitive draws there) while the
    // toggled-off OBV line itself is hidden.
    series.applyOptions({ visible })
    syncTestRenderHook()
  }, [
    chartRef,
    containerRef,
    paneRegistryRef,
    obvSeriesRef,
    obvDivergencePrimitiveRef,
    bars,
    hidden,
    divergences,
    effectiveThemeRef,
    rebuildToken,
    syncTestRenderHook,
  ])
}
