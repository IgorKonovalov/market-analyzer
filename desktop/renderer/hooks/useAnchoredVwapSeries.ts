/**
 * Anchored-VWAP line reconcile (Plan 0092 phase 5).
 *
 * Each `anchored_vwap` overlay draws one line series on the price pane — the
 * volume-weighted typical price accumulated from its anchor bar (explicit
 * `anchor_ts`, or the client dominant-swing auto-anchor). Same add/remove/toggle
 * discipline as `useBbandsSeries`, keyed by `overlayKey`; a legend toggle
 * (`overlayLayerId` in `hidden`) removes the series. Static teal colour — not
 * user-styleable (ADR-0062), so no theme read.
 *
 * MUST be called after the chart-creation + bars effects. `rebuildToken`
 * (candleType) re-runs it after a chart rebuild.
 */
import { useEffect } from 'react'
import type { RefObject } from 'react'
import { LineSeries } from 'lightweight-charts'
import type { IChartApi, ISeriesApi } from 'lightweight-charts'

import { anchoredVwapSeries } from '../lib/anchoredVwap'
import { overlayKey } from '../lib/chartSeries'
import { ANCHORED_VWAP_COLOR, overlayLayerId } from '../lib/overlays'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'

export interface UseAnchoredVwapSeriesParams {
  bars: Bar[]
  overlays: ReadonlyArray<OverlaySpec> | undefined
  hidden: ReadonlySet<string>
  rebuildToken: unknown
}

export function useAnchoredVwapSeries(
  chartRef: RefObject<IChartApi | null>,
  anchoredVwapSeriesRef: RefObject<Map<string, ISeriesApi<'Line'>>>,
  { bars, overlays, hidden, rebuildToken }: UseAnchoredVwapSeriesParams,
): void {
  useEffect(() => {
    const chart = chartRef.current
    const seriesMap = anchoredVwapSeriesRef.current
    if (!chart || !seriesMap) return

    const desired = new Map<string, OverlaySpec>()
    for (const spec of overlays ?? []) {
      if (spec.kind !== 'anchored_vwap') continue
      if (hidden.has(overlayLayerId(spec))) continue
      desired.set(overlayKey(spec), spec)
    }
    for (const [key, series] of seriesMap) {
      if (!desired.has(key)) {
        chart.removeSeries(series)
        seriesMap.delete(key)
      }
    }
    for (const [key, spec] of desired) {
      let series = seriesMap.get(key)
      if (series === undefined) {
        series = chart.addSeries(LineSeries, {
          color: ANCHORED_VWAP_COLOR,
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: false,
        })
        seriesMap.set(key, series)
      }
      series.setData(anchoredVwapSeries(bars, spec.anchor_ts))
    }
  }, [chartRef, anchoredVwapSeriesRef, bars, overlays, hidden, rebuildToken])
}
