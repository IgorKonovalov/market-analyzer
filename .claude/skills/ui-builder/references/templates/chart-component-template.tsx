// Template: desktop/renderer/components/<ChartName>.tsx
//
// Use this for any component that wraps a library managing non-React resources:
// lightweight-charts, Chart.js, Monaco, AG Grid, plain ResizeObserver, setInterval, etc.
//
// The pattern: TWO effects.
//   1. Create the instance ONCE on mount. Return a cleanup that disposes it.
//   2. Push data to the instance when props change. Don't recreate the instance.
//
// Combining the two is the canonical Electron memory leak — every re-render leaks
// the previous instance and its DOM/WebGL/listener resources.

import { useEffect, useRef } from 'react'
import {
  createChart,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type UTCTimestamp,
} from 'lightweight-charts'
import type { Bar } from '@/types/sidecar/types'
import styles from './ChartName.module.css'

interface Props {
  bars: Bar[]
}

export function ChartName({ bars }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)

  // Effect 1: instance lifecycle. Empty deps — runs once on mount.
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: 'var(--color-fg)',
      },
      autoSize: true,
    })
    const series = chart.addCandlestickSeries()

    chartRef.current = chart
    seriesRef.current = series

    return () => {
      // Non-negotiable. Without this, every navigation leaks a chart.
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [])

  // Effect 2: data sync. Runs whenever bars change. Does NOT recreate the chart.
  useEffect(() => {
    seriesRef.current?.setData(bars.map(toLightweightBar))
  }, [bars])

  return (
    <div
      ref={containerRef}
      className={styles.chartContainer}
      role="img"
      aria-label={`Candlestick chart, ${bars.length} bars`}
    />
  )
}

function toLightweightBar(b: Bar): CandlestickData {
  return {
    time: Math.floor(new Date(b.event_ts).getTime() / 1000) as UTCTimestamp,
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
  }
}

// Notes:
// - `toLightweightBar` belongs in `desktop/renderer/api/client.ts` once a second
//   chart imports it. Inline here for the template's stand-alone clarity.
// - `aria-label` describes the chart for screen readers — required for charts.
// - If you need resize beyond `autoSize: true`, add a `ResizeObserver` in a third
//   effect with its own cleanup (`observer.disconnect()`).
// - Don't put `bars` in the first effect's deps — that recreates the chart on
//   every data change. Two effects, two responsibilities.
