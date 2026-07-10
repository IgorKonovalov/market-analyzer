/**
 * Pattern-scan triggers (Plan 0049 phase 8 / Plan 0064 phase 5 — lifted verbatim
 * out of `CandlestickChart` in the Plan 0072 phase 8 decomposition, no behaviour
 * change).
 *
 * Owns the two scan-button status states and the three triggers that sweep the
 * chart's CURRENT visible range via the typed client (bearer injected — never a
 * raw fetch); results arrive over SSE (no second draw path). `recomputeTrendlines`
 * is the silent auto-recompute (mount + debounced range settle); the two
 * `*VisibleRange` triggers are the status-tracked manual buttons.
 */
import { useCallback, useState } from 'react'
import type { RefObject } from 'react'
import type { IChartApi, Time } from 'lightweight-charts'

import { ApiError, api } from '../api/client'
import { visibleRangeIso } from '../lib/chartAxis'

/** Transient state of a scan sweep. The markers/lines arrive via SSE; this only
 * tracks the trigger's ack so the button can show progress / a count / "nothing
 * in view" / an error. */
export type ScanStatus =
  | { kind: 'idle' }
  | { kind: 'scanning' }
  | { kind: 'done'; count: number }
  | { kind: 'empty' }
  | { kind: 'error'; message: string }

export interface UseChartScansParams {
  symbol: string | undefined
  timeframe: string | undefined
}

export interface ChartScans {
  scanStatus: ScanStatus
  chartScanStatus: ScanStatus
  scanVisibleRange: () => Promise<void>
  scanChartPatternsVisibleRange: () => Promise<void>
  recomputeTrendlines: () => Promise<void>
}

export function useChartScans(
  chartRef: RefObject<IChartApi | null>,
  { symbol, timeframe }: UseChartScansParams,
): ChartScans {
  // "Scan patterns" (candlestick markers) trigger state (Plan 0049 phase 8).
  const [scanStatus, setScanStatus] = useState<ScanStatus>({ kind: 'idle' })
  // "Scan chart patterns" (trendline) trigger state (Plan 0064 phase 5). Only the
  // MANUAL button surfaces this; the mount/range auto-recompute stays silent.
  const [chartScanStatus, setChartScanStatus] = useState<ScanStatus>({ kind: 'idle' })

  // Sweep the chart's CURRENT visible range (not the full buffer) for candlestick
  // patterns via POST /scan_patterns; the markers come back over SSE.
  const scanVisibleRange = useCallback(async (): Promise<void> => {
    const chart = chartRef.current
    if (!chart || !symbol || !timeframe) return
    const range = chart.timeScale().getVisibleRange()
    const toIso = (t: Time): string | null =>
      typeof t === 'number' ? new Date(t * 1000).toISOString() : null
    const rangeStart = range ? toIso(range.from) : null
    const rangeEnd = range ? toIso(range.to) : null
    if (rangeStart === null || rangeEnd === null) return
    setScanStatus({ kind: 'scanning' })
    try {
      const ack = await api.scanPatterns({
        symbol,
        timeframe,
        range_start: rangeStart,
        range_end: rangeEnd,
      })
      setScanStatus(
        ack.published && ack.count > 0 ? { kind: 'done', count: ack.count } : { kind: 'empty' },
      )
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Pattern scan failed'
      setScanStatus({ kind: 'error', message })
    }
  }, [chartRef, symbol, timeframe])

  // Recompute chart-pattern TRENDLINES for the current visible range via POST
  // /scan_chart_patterns (Plan 0064 phase 5, ADR-0059). Silent (no status churn):
  // used by the mount + debounced-range auto-recompute so the lines track the bars
  // on screen and survive a reload.
  const recomputeTrendlines = useCallback(async (): Promise<void> => {
    const chart = chartRef.current
    if (!chart || !symbol || !timeframe) return
    const range = visibleRangeIso(chart)
    if (range === null) return
    try {
      await api.scanChartPatterns({ symbol, timeframe, ...range })
    } catch (err) {
      // Auto-recompute is best-effort: a failed refresh just leaves the current
      // lines in place. Log, don't surface (the manual trigger reports errors).
      console.warn('[CandlestickChart] trendline recompute failed', err)
    }
  }, [chartRef, symbol, timeframe])

  // Manual "Scan chart patterns" trigger (Plan 0064 phase 5): same sweep as the
  // auto-recompute but status-tracked for button feedback.
  const scanChartPatternsVisibleRange = useCallback(async (): Promise<void> => {
    const chart = chartRef.current
    if (!chart || !symbol || !timeframe) return
    const range = visibleRangeIso(chart)
    if (range === null) return
    setChartScanStatus({ kind: 'scanning' })
    try {
      const ack = await api.scanChartPatterns({ symbol, timeframe, ...range })
      setChartScanStatus(
        ack.published && ack.count > 0 ? { kind: 'done', count: ack.count } : { kind: 'empty' },
      )
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Chart-pattern scan failed'
      setChartScanStatus({ kind: 'error', message })
    }
  }, [chartRef, symbol, timeframe])

  return {
    scanStatus,
    chartScanStatus,
    scanVisibleRange,
    scanChartPatternsVisibleRange,
    recomputeTrendlines,
  }
}
