/**
 * Bollinger Bands three-line reconcile (Plan 0082 phase 2, ADR-0077).
 *
 * Each `bbands` overlay draws three line series on the price pane — upper /
 * middle / lower — sharing one static colour (the middle band dashed so it reads
 * distinctly). Same add/remove/toggle discipline as the supertrend pair, keyed by
 * `overlayKey`; a legend toggle (`overlayLayerId` in the hidden set) removes all
 * three. The three series are NOT reported to the render test-hook (they live in
 * their own ref), matching `useSupertrendSeries`. The line colour is static
 * (`BBANDS_LINE_COLOR`), so unlike the themed overlays this needs no theme read
 * and no restyle pass.
 *
 * MUST be called after the chart-creation + bars effects. `rebuildToken`
 * (candleType) re-runs it after a chart rebuild.
 */
import { useEffect } from 'react'
import type { RefObject } from 'react'
import { LineSeries, LineStyle } from 'lightweight-charts'
import type { IChartApi, ISeriesApi } from 'lightweight-charts'

import { computeBbands } from '../lib/indicators'
import { overlayKey } from '../lib/chartSeries'
import { BBANDS_LINE_COLOR, overlayLayerId } from '../lib/overlays'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'

type BbandsTriple = {
  upper: ISeriesApi<'Line'>
  middle: ISeriesApi<'Line'>
  lower: ISeriesApi<'Line'>
}

/** Defaults mirror the pydantic `bbands` descriptor (Plan 0082 ph1) and the
 * Python `bollinger` signature: period 20, std-dev multiplier `k` 2.0. */
const BBANDS_DEFAULT_PERIOD = 20
const BBANDS_DEFAULT_K = 2

export interface UseBbandsSeriesParams {
  bars: Bar[]
  overlays: ReadonlyArray<OverlaySpec> | undefined
  hidden: ReadonlySet<string>
  rebuildToken: unknown
}

export function useBbandsSeries(
  chartRef: RefObject<IChartApi | null>,
  bbandsSeriesRef: RefObject<Map<string, BbandsTriple>>,
  { bars, overlays, hidden, rebuildToken }: UseBbandsSeriesParams,
): void {
  useEffect(() => {
    const chart = chartRef.current
    const bbandsSeries = bbandsSeriesRef.current
    if (!chart || !bbandsSeries) return

    const desired = new Map<string, OverlaySpec>()
    for (const spec of overlays ?? []) {
      if (spec.kind !== 'bbands') continue
      if (hidden.has(overlayLayerId(spec))) continue
      desired.set(overlayKey(spec), spec)
    }
    for (const [key, entry] of bbandsSeries) {
      if (!desired.has(key)) {
        chart.removeSeries(entry.upper)
        chart.removeSeries(entry.middle)
        chart.removeSeries(entry.lower)
        bbandsSeries.delete(key)
      }
    }
    for (const [key, spec] of desired) {
      let entry = bbandsSeries.get(key)
      if (entry === undefined) {
        const band = {
          color: BBANDS_LINE_COLOR,
          lineWidth: 1 as const,
          priceLineVisible: false,
          lastValueVisible: false,
        }
        const upper = chart.addSeries(LineSeries, band)
        const middle = chart.addSeries(LineSeries, { ...band, lineStyle: LineStyle.Dashed })
        const lower = chart.addSeries(LineSeries, band)
        entry = { upper, middle, lower }
        bbandsSeries.set(key, entry)
      }
      const data = computeBbands(
        bars,
        spec.period ?? BBANDS_DEFAULT_PERIOD,
        spec.multiplier ?? BBANDS_DEFAULT_K,
      )
      entry.upper.setData(data.upper)
      entry.middle.setData(data.middle)
      entry.lower.setData(data.lower)
    }
  }, [chartRef, bbandsSeriesRef, bars, overlays, hidden, rebuildToken])
}
