/**
 * Plan 0064 phase 5 (ADR-0059): re-derive chart-pattern trendlines from the
 * bars currently on screen. Trendlines are derived, never persisted, so the
 * renderer recomputes them via `POST /scan_chart_patterns` on chart load and
 * whenever the visible range settles after a pan/zoom — this closes the
 * durability gap (reload → lines return) without a persistence table.
 *
 * A single debounced timer serves both triggers: mount schedules the first
 * recompute, and every `visibleLogicalRangeChange` (including the initial
 * fitContent settle) resets it — so a burst of pan/zoom events coalesces to ONE
 * call after the viewport goes quiet. Mirrors `useLazyHistoryTrigger`: the chart
 * instance is owned by the component (lifecycle + dispose per ADR-0008); this
 * hook receives its ref and MUST be called after the chart-creation effect so
 * the ref is populated on mount.
 */
import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'
import type { IChartApi } from 'lightweight-charts'

export const DEFAULT_RECOMPUTE_DEBOUNCE_MS = 400

export interface UseChartPatternRecomputeOptions {
  /** When false, timers still run but never invoke the callback (e.g. no
   * symbol/timeframe yet). */
  enabled: boolean
  /** Re-derive the trendlines for the current visible range. */
  onRecompute: () => void
  /** Quiet period after the last range change before recomputing. */
  debounceMs?: number
  /** Changes when the chart instance is rebuilt (Plan 0068 phase 4: a candle-type
   * switch). `chartRef` is a stable object, so this token is what tells the effect
   * to re-subscribe onto the fresh chart's time scale and re-schedule. */
  rebuildToken?: unknown
}

export function useChartPatternRecompute(
  chartRef: RefObject<IChartApi | null>,
  {
    enabled,
    onRecompute,
    debounceMs = DEFAULT_RECOMPUTE_DEBOUNCE_MS,
    rebuildToken,
  }: UseChartPatternRecomputeOptions,
): void {
  // Latest-value refs so the subscription registers once (and the cleanup
  // unsubscribes the same handler) without re-subscribing on every render.
  const enabledRef = useRef(enabled)
  enabledRef.current = enabled
  const onRecomputeRef = useRef(onRecompute)
  onRecomputeRef.current = onRecompute
  const debounceRef = useRef(debounceMs)
  debounceRef.current = debounceMs

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const timeScale = chart.timeScale()
    let timer: ReturnType<typeof setTimeout> | null = null

    const schedule = (): void => {
      if (timer !== null) clearTimeout(timer)
      timer = setTimeout(() => {
        timer = null
        if (enabledRef.current) onRecomputeRef.current()
      }, debounceRef.current)
    }

    schedule() // mount: fire once after the view settles
    timeScale.subscribeVisibleLogicalRangeChange(schedule)
    return () => {
      if (timer !== null) clearTimeout(timer)
      timeScale.unsubscribeVisibleLogicalRangeChange(schedule)
    }
  }, [chartRef, rebuildToken])
}
