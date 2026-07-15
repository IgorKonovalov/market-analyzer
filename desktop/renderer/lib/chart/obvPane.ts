/**
 * ObvPaneReconciler — the lazy OBV sub-pane (Plan 0105 phase 3), folded out of
 * `useObvPane` into the controller (Plan 0098 thin-A). Lazy-create the pane + line
 * series + divergence primitive when wanted, remove them when not, keeping OBV the
 * FIRST managed sub-pane (registered at slot 0 so oscillators stay 2..N). Wanted =
 * the OBV legend row is visible OR an obv divergence needs the pane (the required-
 * pane rule: a divergence's oscillator segment must have a pane even when the series
 * is toggled off — then the pane exists, the line is hidden, only the primitive
 * draws). Pure imperative wiring; no React.
 */
import { LineSeries } from 'lightweight-charts'
import type { IChartApi, ISeriesApi, LineWidth } from 'lightweight-charts'

import {
  OBV_LAYER_ID,
  OBV_PANE_HEIGHT,
  OBV_PANE_ID,
  OBV_SCALE_ID,
  chartColorsFrom,
} from '../chartSeries'
import { resolveChartStyle } from '../chartStyle'
import { DivergencePrimitive, readDivergenceColors } from '../divergences'
import { PaneLabelPrimitive, paneLabelFor } from '../paneLabel'
import { computeObv } from '../volume'
import type { PaneRegistry } from '../panes'
import type { EffectiveTheme } from '../theme'
import type { Bar } from '../../types/sidecar/bar'
import type { Divergence } from '../../types/events'
import type { MutRef } from './ref'

/** The managed-order slot OBV claims — the first sub-pane below price, ahead of every
 * oscillator pane (the Plan 0095/0091 pane-order invariant, under lazy re-create). */
const OBV_PANE_SLOT = 0

export interface ObvReconcileParams {
  bars: Bar[]
  hidden: ReadonlySet<string>
  divergences: ReadonlyArray<Divergence>
  theme: EffectiveTheme
}

export class ObvPaneReconciler {
  readonly seriesRef: MutRef<ISeriesApi<'Line'>> = { current: null }
  readonly divergencePrimitiveRef: MutRef<DivergencePrimitive> = { current: null }

  reconcile(
    chart: IChartApi | null,
    container: HTMLDivElement | null,
    registry: PaneRegistry | null,
    { bars, hidden, divergences, theme }: ObvReconcileParams,
  ): void {
    if (chart === null || registry === null || container === null) return

    const visible = !hidden.has(OBV_LAYER_ID)
    const divergenceNeedsPane = divergences.some((d) => d.oscillator === 'obv')
    const wanted = visible || divergenceNeedsPane

    if (!wanted) {
      const series = this.seriesRef.current
      if (series !== null) {
        chart.removeSeries(series)
        registry.remove(OBV_PANE_ID)
        this.seriesRef.current = null
        this.divergencePrimitiveRef.current = null
      }
      return
    }

    let series = this.seriesRef.current
    if (series === null) {
      const style = resolveChartStyle(container, theme)
      const colors = chartColorsFrom(style)
      const paneIndex = registry.ensure(OBV_PANE_ID, OBV_PANE_SLOT)
      series = chart.addSeries(
        LineSeries,
        {
          priceScaleId: OBV_SCALE_ID,
          color: colors.obv,
          lineWidth: style.widths.obv as LineWidth,
          priceLineVisible: false,
          lastValueVisible: false,
        },
        paneIndex,
      )
      registry.pane(OBV_PANE_ID)?.setHeight(OBV_PANE_HEIGHT)
      const primitive = new DivergencePrimitive('oscillator', readDivergenceColors(container))
      series.attachPrimitive(primitive)
      series.attachPrimitive(new PaneLabelPrimitive(paneLabelFor('obv')))
      this.seriesRef.current = series
      this.divergencePrimitiveRef.current = primitive
    }
    series.setData(computeObv(bars))
    // Divergence-only mode: the pane stays (the primitive draws) while the toggled-
    // off OBV line itself is hidden.
    series.applyOptions({ visible })
  }

  clear(): void {
    this.seriesRef.current = null
    this.divergencePrimitiveRef.current = null
  }
}
