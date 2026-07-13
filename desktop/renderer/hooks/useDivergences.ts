/**
 * Divergence-segment reconcile (Plan 0091 phase 9, ADR-0090).
 *
 * FEEDS the already-attached `DivergencePrimitive`s their divergences + theme-
 * resolved colours on every change. It does NOT attach the primitives: the
 * price-pane and OBV-pane primitives are created + `attachPrimitive`'d in the
 * component's chart-creation effect (mirroring the trendline primitive), and each
 * oscillator pane's primitive is attached by `useOscillatorPanes` at pane creation
 * — so every primitive's lifecycle is tied to its series and disposed by
 * `chart.remove()` / pane removal (the Plan 0064 stranding-bug fix, reused here).
 *
 * A divergence draws as two segments: the price primitive draws every divergence's
 * `price_pivots` (on pane 0); each oscillator/OBV primitive draws only the
 * `oscillator_pivots` of divergences whose oscillator maps to that pane. Pane
 * existence is ensured upstream — the component derives the required oscillator
 * kinds from the divergences and passes them to `useOscillatorPanes`.
 *
 * MUST be called after the chart-creation effect and after `useOscillatorPanes`,
 * so the primitives exist before this feed runs.
 */
import { useEffect } from 'react'
import type { RefObject } from 'react'

import type { EffectiveTheme } from '../lib/theme'
import {
  DivergencePrimitive,
  divergenceOscillatorToPaneKind,
  fallbackDivergenceColors,
  readDivergenceColors,
} from '../lib/divergences'
import type { OscillatorPaneEntry } from './useOscillatorPanes'
import type { Divergence } from '../types/events'

export interface UseDivergencesParams {
  divergences: ReadonlyArray<Divergence>
  effectiveTheme: EffectiveTheme
  /** Re-runs the feed after a chart rebuild (candle-type switch) attaches fresh,
   * empty primitives — same role as `useTrendlines`' rebuild token. */
  rebuildToken?: unknown
}

export function useDivergences(
  containerRef: RefObject<HTMLDivElement>,
  divergencePricePrimitiveRef: RefObject<DivergencePrimitive>,
  obvDivergencePrimitiveRef: RefObject<DivergencePrimitive>,
  oscillatorPanesRef: RefObject<Map<string, OscillatorPaneEntry>>,
  { divergences, rebuildToken }: UseDivergencesParams,
): void {
  useEffect(() => {
    const container = containerRef.current
    const colors = container ? readDivergenceColors(container) : fallbackDivergenceColors()

    // Price pane: every divergence's price-pivot segment.
    const pricePrimitive = divergencePricePrimitiveRef.current
    if (pricePrimitive) {
      pricePrimitive.setColors(colors)
      pricePrimitive.setDivergences(divergences)
    }

    // OBV base pane: only obv divergences.
    const obvPrimitive = obvDivergencePrimitiveRef.current
    if (obvPrimitive) {
      obvPrimitive.setColors(colors)
      obvPrimitive.setDivergences(divergences.filter((d) => d.oscillator === 'obv'))
    }

    // Each oscillator pane: only the divergences whose oscillator maps to it.
    const panes = oscillatorPanesRef.current
    if (panes) {
      for (const entry of panes.values()) {
        entry.divergencePrimitive.setColors(colors)
        entry.divergencePrimitive.setDivergences(
          divergences.filter((d) => divergenceOscillatorToPaneKind(d.oscillator) === entry.kind),
        )
      }
    }
  }, [
    containerRef,
    divergencePricePrimitiveRef,
    obvDivergencePrimitiveRef,
    oscillatorPanesRef,
    divergences,
    rebuildToken,
  ])
}

/** The oscillator kinds a divergence set needs a pane for (Plan 0091 phase 9):
 * every referenced oscillator EXCEPT `obv` (which uses the always-on OBV base pane,
 * not a `useOscillatorPanes` pane). Fed to `useOscillatorPanes`' `requiredKinds` so
 * the panes are ensured before `useDivergences` feeds them. */
export function requiredOscillatorKindsFor(
  divergences: ReadonlyArray<Divergence>,
): Set<'rsi' | 'macd' | 'mfi'> {
  const kinds = new Set<'rsi' | 'macd' | 'mfi'>()
  for (const d of divergences) {
    if (d.oscillator === 'obv') continue
    kinds.add(divergenceOscillatorToPaneKind(d.oscillator) as 'rsi' | 'macd' | 'mfi')
  }
  return kinds
}
