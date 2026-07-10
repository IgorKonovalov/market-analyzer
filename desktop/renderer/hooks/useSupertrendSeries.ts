/**
 * Supertrend two-masked-series reconcile (Plan 0049 phase 9 — lifted verbatim out
 * of `CandlestickChart`'s bars effect in the Plan 0072 phase 8 decomposition, no
 * behaviour change).
 *
 * Each supertrend overlay draws two masked line series (up=bullish, down=bearish)
 * so the trailing-stop line flips colour at trend changes. Same add/remove/toggle
 * discipline as the generic overlays. The two series are NOT reported to the
 * render test-hook (they live in their own ref), so this does not call
 * `syncTestRenderHook`. Theme read off the ref so a flip doesn't re-create them —
 * the restyle effect recolours the pair in place.
 *
 * MUST be called after the chart-creation + bars effects. `rebuildToken`
 * (candleType) re-runs it after a chart rebuild.
 */
import { useEffect } from 'react'
import type { RefObject } from 'react'
import type { IChartApi, ISeriesApi } from 'lightweight-charts'

import { overlayKey } from '../lib/chartSeries'
import { resolveChartStyle } from '../lib/chartStyle'
import { DEFAULT_MARKER_COLORS } from '../lib/markers'
import { computeSupertrend, overlayLayerId, supertrendBands } from '../lib/overlays'
import type { EffectiveTheme } from '../lib/theme'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'

type SupertrendPair = { up: ISeriesApi<'Line'>; down: ISeriesApi<'Line'> }

export interface UseSupertrendSeriesParams {
  bars: Bar[]
  overlays: ReadonlyArray<OverlaySpec> | undefined
  hidden: ReadonlySet<string>
  effectiveThemeRef: RefObject<EffectiveTheme>
  rebuildToken: unknown
}

export function useSupertrendSeries(
  chartRef: RefObject<IChartApi | null>,
  containerRef: RefObject<HTMLDivElement>,
  supertrendSeriesRef: RefObject<Map<string, SupertrendPair>>,
  { bars, overlays, hidden, effectiveThemeRef, rebuildToken }: UseSupertrendSeriesParams,
): void {
  useEffect(() => {
    const chart = chartRef.current
    const supertrendSeries = supertrendSeriesRef.current
    if (!chart || !supertrendSeries) return

    const overlayStyle =
      containerRef.current && effectiveThemeRef.current
        ? resolveChartStyle(containerRef.current, effectiveThemeRef.current)
        : null
    const upColor = overlayStyle?.colors.markerBullish ?? DEFAULT_MARKER_COLORS.bullish
    const downColor = overlayStyle?.colors.markerBearish ?? DEFAULT_MARKER_COLORS.bearish

    const desiredSt = new Map<string, OverlaySpec>()
    for (const spec of overlays ?? []) {
      if (spec.kind !== 'supertrend') continue
      if (hidden.has(overlayLayerId(spec))) continue
      desiredSt.set(overlayKey(spec), spec)
    }
    for (const [key, entry] of supertrendSeries) {
      if (!desiredSt.has(key)) {
        chart.removeSeries(entry.up)
        chart.removeSeries(entry.down)
        supertrendSeries.delete(key)
      }
    }
    for (const [key, spec] of desiredSt) {
      let entry = supertrendSeries.get(key)
      if (entry === undefined) {
        const lineOpts = { lineWidth: 2 as const, priceLineVisible: false, lastValueVisible: false }
        const up = chart.addLineSeries({ color: upColor, ...lineOpts })
        const down = chart.addLineSeries({ color: downColor, ...lineOpts })
        entry = { up, down }
        supertrendSeries.set(key, entry)
      } else {
        entry.up.applyOptions({ color: upColor })
        entry.down.applyOptions({ color: downColor })
      }
      const points = computeSupertrend(bars, spec.period ?? 10, spec.multiplier ?? 3)
      const bands = supertrendBands(points)
      entry.up.setData(bands.up)
      entry.down.setData(bands.down)
    }
  }, [
    chartRef,
    containerRef,
    supertrendSeriesRef,
    bars,
    overlays,
    hidden,
    effectiveThemeRef,
    rebuildToken,
  ])
}
