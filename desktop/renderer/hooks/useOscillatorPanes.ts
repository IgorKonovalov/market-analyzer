/**
 * Oscillator sub-pane reconcile (Plan 0091 phase 6) — the reusable wrapper the
 * plan calls for, built on the v5 panes API via `lib/panes.ts` (`PaneRegistry`).
 *
 * Each active oscillator overlay (stochastic / stoch_rsi / cci / williams_r / roc)
 * draws in its OWN real pane below the price pane, independently auto-scaled with a
 * shared time axis and one crosshair — not a `scaleMargins` band. This hook
 * reconciles the desired oscillator set against the live panes: create-or-reuse a
 * pane per oscillator by stable id, set its height, push the mirrored series data
 * (`lib/oscillators`), and tear the pane down when the oscillator is removed or its
 * legend row is toggled off (the wrapper's documented contract — toggle-off removes
 * the pane, freeing its vertical space; toggle-on re-adds it).
 *
 * MUST be called after the chart-creation effect so the chart + `PaneRegistry` refs
 * exist (the same registry the OBV pane uses, so OBV stays pane 1 and oscillators
 * take panes 2..N). `rebuildToken` (candleType) re-runs it after a chart rebuild.
 */
import { useEffect } from 'react'
import type { RefObject } from 'react'
import { LineSeries } from 'lightweight-charts'
import type { IChartApi, ISeriesApi, LineData, LineWidth } from 'lightweight-charts'

import {
  computeCci,
  computeMacdHist,
  computeRoc,
  computeRsi,
  computeStochastic,
  computeStochasticRsi,
  computeWilliamsR,
} from '../lib/oscillators'
import { isOscillatorOverlay, overlayColorFor, overlayLayerId } from '../lib/overlays'
import { computeAccumulationDistribution, computeChaikinMoneyFlow, computeMfi } from '../lib/volume'
import { DivergencePrimitive, fallbackDivergenceColors } from '../lib/divergences'
import type { PaneRegistry } from '../lib/panes'
import type { Bar } from '../types/sidecar/bar'
import type { OverlayKind, OverlaySpec } from '../types/events'

/** Height (px) of each oscillator sub-pane — matches the OBV pane so the stack of
 * sub-panes reads as one consistent band system below the price pane. */
export const OSCILLATOR_PANE_HEIGHT = 110

/** The `%D` signal-line colour for the Stochastic pane (the `%K` line takes the
 * registry swatch colour). Muted amber so the two lines are distinguishable. */
const STOCH_SIGNAL_COLOR = '#f59e0b'
const OSCILLATOR_LINE_WIDTH = 2 as LineWidth

/** Stable pane id for an oscillator kind — one pane per kind (Plan 0091). */
export function oscillatorPaneId(kind: OverlayKind): string {
  return `osc:${kind}`
}

export interface OscillatorPaneEntry {
  kind: OverlayKind
  paneId: string
  /** One line for cci/williams_r/roc/stoch_rsi; two (%K, %D) for stochastic. */
  series: ISeriesApi<'Line'>[]
  /** The divergence primitive attached to this pane's primary series (Plan 0091
   * phase 9): `useDivergences` feeds it the oscillator-pivot segments of any
   * divergence for this oscillator. Disposed with the pane's series on removal. */
  divergencePrimitive: DivergencePrimitive
}

/** The oscillator's line data — one array per drawn line (stochastic returns
 * `[%K, %D]`; the others a single line). Mirrors `lib/oscillators`. */
function computeOscillatorLines(kind: OverlayKind, bars: Bar[]): LineData[][] {
  switch (kind) {
    case 'stochastic': {
      const { k, d } = computeStochastic(bars)
      return [k, d]
    }
    case 'stoch_rsi':
      return [computeStochasticRsi(bars)]
    case 'cci':
      return [computeCci(bars)]
    case 'williams_r':
      return [computeWilliamsR(bars)]
    case 'roc':
      return [computeRoc(bars)]
    case 'mfi':
      return [computeMfi(bars)]
    case 'cmf':
      return [computeChaikinMoneyFlow(bars)]
    case 'ad_line':
      return [computeAccumulationDistribution(bars)]
    case 'rsi':
      return [computeRsi(bars)]
    case 'macd':
      return [computeMacdHist(bars)]
    default:
      return []
  }
}

/** Create the pane's line series (one, or two for stochastic %K/%D) on `paneIndex`. */
function createOscillatorSeries(
  chart: IChartApi,
  kind: OverlayKind,
  color: string,
  paneIndex: number,
): ISeriesApi<'Line'>[] {
  const lineCount = kind === 'stochastic' ? 2 : 1
  const series: ISeriesApi<'Line'>[] = []
  for (let i = 0; i < lineCount; i++) {
    series.push(
      chart.addSeries(
        LineSeries,
        {
          color: i === 0 ? color : STOCH_SIGNAL_COLOR,
          lineWidth: OSCILLATOR_LINE_WIDTH,
          priceLineVisible: false,
          lastValueVisible: false,
        },
        paneIndex,
      ),
    )
  }
  return series
}

export interface UseOscillatorPanesParams {
  bars: Bar[]
  overlays: ReadonlyArray<OverlaySpec> | undefined
  hidden: ReadonlySet<string>
  /** Oscillator kinds a divergence needs a pane for (Plan 0091 phase 9): these
   * panes are ensured (created if absent) even when the user has not added — or has
   * toggled off — that oscillator overlay, so the divergence's oscillator segment
   * always has a pane to draw on. `obv` divergences use the always-on OBV base pane
   * and are NOT included here. */
  requiredKinds: ReadonlySet<OverlayKind>
  rebuildToken: unknown
  syncTestRenderHook: () => void
}

export function useOscillatorPanes(
  chartRef: RefObject<IChartApi | null>,
  paneRegistryRef: RefObject<PaneRegistry | null>,
  oscillatorPanesRef: RefObject<Map<string, OscillatorPaneEntry>>,
  {
    bars,
    overlays,
    hidden,
    requiredKinds,
    rebuildToken,
    syncTestRenderHook,
  }: UseOscillatorPanesParams,
): void {
  useEffect(() => {
    const chart = chartRef.current
    const registry = paneRegistryRef.current
    const panes = oscillatorPanesRef.current
    if (!chart || !registry || !panes) return

    // The oscillators the chart should show now: one pane per kind, minus any whose
    // legend row is toggled off (removed below, re-added when re-checked).
    const desired = new Map<string, OverlaySpec>()
    for (const spec of overlays ?? []) {
      if (!isOscillatorOverlay(spec.kind)) continue
      if (hidden.has(overlayLayerId(spec))) continue
      desired.set(oscillatorPaneId(spec.kind), spec)
    }
    // Ensure a pane for every oscillator a divergence needs (Plan 0091 phase 9),
    // even if the user hasn't added it or has toggled it off — the oscillator
    // segment must have a pane to draw on. A synthetic `{ kind }` spec suffices.
    for (const kind of requiredKinds) {
      if (isOscillatorOverlay(kind)) desired.set(oscillatorPaneId(kind), { kind })
    }

    // Remove panes no longer wanted (gone from overlays, or toggled off).
    for (const [paneId, entry] of panes) {
      if (!desired.has(paneId)) {
        for (const s of entry.series) chart.removeSeries(s)
        registry.remove(paneId)
        panes.delete(paneId)
      }
    }

    // Create new panes + recompute data for all kept ones (bars may have moved).
    for (const [paneId, spec] of desired) {
      let entry = panes.get(paneId)
      if (entry === undefined) {
        const paneIndex = registry.ensure(paneId)
        const series = createOscillatorSeries(chart, spec.kind, overlayColorFor(spec), paneIndex)
        registry.pane(paneId)?.setHeight(OSCILLATOR_PANE_HEIGHT)
        // Attach the divergence primitive to the pane's primary series so its
        // lifecycle is the series' (disposed on pane removal); `useDivergences`
        // feeds it. Draws nothing until fed.
        const divergencePrimitive = new DivergencePrimitive(
          'oscillator',
          fallbackDivergenceColors(),
        )
        series[0].attachPrimitive(divergencePrimitive)
        entry = { kind: spec.kind, paneId, series, divergencePrimitive }
        panes.set(paneId, entry)
      }
      const lines = computeOscillatorLines(spec.kind, bars)
      entry.series.forEach((s, i) => s.setData(lines[i] ?? []))
    }

    syncTestRenderHook()
  }, [
    chartRef,
    paneRegistryRef,
    oscillatorPanesRef,
    bars,
    overlays,
    hidden,
    requiredKinds,
    rebuildToken,
    syncTestRenderHook,
  ])
}
