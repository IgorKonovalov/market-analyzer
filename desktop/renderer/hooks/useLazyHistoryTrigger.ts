/**
 * Plan 0030 phase 2: fire a callback when the chart's visible logical range
 * scrolls within a threshold of the buffer's left edge (logical index 0), so
 * the parent can fetch + prepend older bars.
 *
 * Subscribes to `timeScale().subscribeVisibleLogicalRangeChange`. To avoid a
 * trigger storm while the viewport is parked at the edge, it fires once per
 * *inward* crossing: the callback runs only when the range enters the
 * near-edge zone from outside it, not on every event while it stays there.
 *
 * The chart instance is owned by the component (lifecycle + dispose stay there
 * per ADR-0008); this hook receives its ref. It MUST be called after the
 * component's chart-creation effect so the ref is populated on mount.
 */
import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'
import type { IChartApi, LogicalRange } from 'lightweight-charts'

export const DEFAULT_LEFT_EDGE_THRESHOLD_BARS = 10

export interface UseLazyHistoryTriggerOptions {
  /** When false, crossings are tracked but never invoke the callback. */
  enabled: boolean
  onReachLeftEdge: () => void
  /** Bars from logical index 0 that count as "at the left edge". */
  thresholdBars?: number
  /** Changes when the chart instance is rebuilt (Plan 0068 phase 4: a candle-type
   * switch). `chartRef` is a stable object, so its identity alone can't tell this
   * effect to re-subscribe onto the fresh chart's time scale — this token does. */
  rebuildToken?: unknown
}

export function useLazyHistoryTrigger(
  chartRef: RefObject<IChartApi | null>,
  {
    enabled,
    onReachLeftEdge,
    thresholdBars = DEFAULT_LEFT_EDGE_THRESHOLD_BARS,
    rebuildToken,
  }: UseLazyHistoryTriggerOptions,
): void {
  // Latest-value refs so the subscription registers once (and the cleanup
  // unsubscribes the same handler) without re-subscribing on every render.
  const enabledRef = useRef(enabled)
  enabledRef.current = enabled
  const onReachRef = useRef(onReachLeftEdge)
  onReachRef.current = onReachLeftEdge
  const thresholdRef = useRef(thresholdBars)
  thresholdRef.current = thresholdBars
  // Whether the previous range already sat in the near-edge zone — gates the
  // fire to the inward crossing only.
  const wasNearRef = useRef(false)

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const timeScale = chart.timeScale()

    const handler = (range: LogicalRange | null): void => {
      const near = range !== null && range.from <= thresholdRef.current
      if (near && !wasNearRef.current && enabledRef.current) {
        onReachRef.current()
      }
      wasNearRef.current = near
    }

    timeScale.subscribeVisibleLogicalRangeChange(handler)
    return () => {
      timeScale.unsubscribeVisibleLogicalRangeChange(handler)
    }
  }, [chartRef, rebuildToken])
}
