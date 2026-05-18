/**
 * Lightweight-charts wrapper. Two effects, two responsibilities:
 *   1. Create the chart once on mount; dispose on unmount.
 *   2. Push data when `bars` change; never recreate the chart for new data.
 *
 * Disposing on unmount is non-negotiable — without it every navigation leaks
 * a Canvas/WebGL context. See ui-builder/references/best-practices.md.
 */
import { useEffect, useRef } from 'react'
import { ColorType, createChart } from 'lightweight-charts'
import type { IChartApi, ISeriesApi } from 'lightweight-charts'

import { toLightweightBar } from '../api/client'
import type { Bar } from '../types/sidecar/bar'
import styles from './CandlestickChart.module.css'

interface Props {
  bars: Bar[]
  ariaLabel?: string
}

export function CandlestickChart({ bars, ariaLabel }: Props): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    // lightweight-charts hands these strings to canvas APIs that don't
    // resolve CSS variables; passing `var(--color-fg)` paints with the
    // browser's invalid-color fallback. Read the computed values once at
    // mount and feed real color strings in.
    const computed = getComputedStyle(container)
    const textColor = computed.getPropertyValue('--color-fg').trim() || '#1a1a1a'
    const borderColor = computed.getPropertyValue('--color-border').trim() || '#e5e5e5'

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor,
      },
      grid: {
        vertLines: { color: borderColor },
        horzLines: { color: borderColor },
      },
      timeScale: {
        timeVisible: false,
        secondsVisible: false,
      },
      autoSize: true,
    })
    const series = chart.addCandlestickSeries()

    chartRef.current = chart
    seriesRef.current = series

    return () => {
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [])

  useEffect(() => {
    const series = seriesRef.current
    if (!series) return
    series.setData(bars.map(toLightweightBar))
    chartRef.current?.timeScale().fitContent()
  }, [bars])

  return (
    <div
      ref={containerRef}
      className={styles.chartContainer}
      data-testid="candlestick-chart"
      role="img"
      aria-label={ariaLabel ?? `Candlestick chart, ${bars.length} bars`}
    />
  )
}
