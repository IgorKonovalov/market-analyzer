/**
 * Fibonacci-grid + classic-pivot horizontal lines (Plan 0092 phase 5).
 *
 * `fibonacci` and `pivot_points` overlays draw as labeled horizontal price lines
 * on the main candlestick series (the same `createPriceLine` primitive
 * `usePriceLines` uses for S/R levels), not line series — a fib grid is a set of
 * ratio lines, a pivot set is P/R1-3/S1-3. Both are client-computed from the bars
 * the chart holds (auto-anchored, or from the overlay's explicit anchor/method),
 * so a bars change recomputes the prices in place. A legend toggle
 * (`overlayLayerId` in `hidden`) removes all of that overlay's lines; re-checking
 * re-creates them. Static colours (fib violet, pivot amber) — not user-styleable
 * (ADR-0062), so no theme read.
 *
 * MUST be called after the chart-creation effect so `seriesRef` is populated.
 * `rebuildToken` (candleType) re-creates the lines on the fresh series.
 */
import { useEffect } from 'react'
import type { RefObject } from 'react'
import type { IPriceLine } from 'lightweight-charts'

import type { MainSeries } from '../lib/chartSeries'
import { fibonacciGrid } from '../lib/fibonacci'
import { FIB_LINE_COLOR, PIVOT_LINE_COLOR, overlayLayerId } from '../lib/overlays'
import { pivotLevelLines, pivotPoints } from '../lib/pivots'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'

interface DesiredLine {
  price: number
  color: string
  title: string
}

export interface UseStructureLevelsParams {
  bars: Bar[]
  overlays: ReadonlyArray<OverlaySpec> | undefined
  hidden: ReadonlySet<string>
  rebuildToken: unknown
}

export function useStructureLevels(
  seriesRef: RefObject<MainSeries | null>,
  structureLinesRef: RefObject<Map<string, IPriceLine>>,
  { bars, overlays, hidden, rebuildToken }: UseStructureLevelsParams,
): void {
  useEffect(() => {
    const series = seriesRef.current
    const structureLines = structureLinesRef.current
    if (!series || !structureLines) return

    const desired = new Map<string, DesiredLine>()
    for (const spec of overlays ?? []) {
      if (spec.kind !== 'fibonacci' && spec.kind !== 'pivot_points') continue
      const layer = overlayLayerId(spec)
      if (hidden.has(layer)) continue
      if (spec.kind === 'fibonacci') {
        const grid = fibonacciGrid(bars, spec)
        if (grid === null) continue
        for (const level of grid.levels) {
          desired.set(`${layer}:${level.ratio}`, {
            price: level.price,
            color: FIB_LINE_COLOR,
            title: `Fib ${level.ratio}`,
          })
        }
      } else {
        const pivots = pivotPoints(bars, spec.method ?? 'floor')
        if (pivots === null) continue
        for (const line of pivotLevelLines(pivots)) {
          desired.set(`${layer}:${line.label}`, {
            price: line.price,
            color: PIVOT_LINE_COLOR,
            title: line.label,
          })
        }
      }
    }

    for (const [id, line] of structureLines) {
      if (!desired.has(id)) {
        series.removePriceLine(line)
        structureLines.delete(id)
      }
    }
    for (const [id, spec] of desired) {
      const existing = structureLines.get(id)
      if (existing === undefined) {
        structureLines.set(
          id,
          series.createPriceLine({
            price: spec.price,
            color: spec.color,
            axisLabelVisible: true,
            title: spec.title,
          }),
        )
      } else {
        // A bars change re-anchors the grid — update the price in place.
        existing.applyOptions({ price: spec.price, color: spec.color, title: spec.title })
      }
    }
  }, [seriesRef, structureLinesRef, bars, overlays, hidden, rebuildToken])
}
