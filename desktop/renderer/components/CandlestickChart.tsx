/**
 * Lightweight-charts wrapper. Three effects, three responsibilities:
 *   1. Create the chart once on mount; dispose on unmount.
 *   2. Push data when `bars` change; never recreate the chart for new data.
 *   3. Push markers when `annotations` change; layer onto the live series.
 *
 * Disposing on unmount is non-negotiable — without it every navigation leaks
 * a Canvas/WebGL context. See ui-builder/references/best-practices.md.
 */
import { useEffect, useMemo, useRef } from 'react'
import { ColorType, createChart } from 'lightweight-charts'
import type { IChartApi, ISeriesApi, SeriesMarker, UTCTimestamp } from 'lightweight-charts'

import { toLightweightBar } from '../api/client'
import type { Annotation } from '../types/sidecar/annotation'
import type { Bar } from '../types/sidecar/bar'
import styles from './CandlestickChart.module.css'

const MARKER_LABEL_MAX = 24
const BULLISH_COLOR = '#16a34a'
const BEARISH_COLOR = '#dc2626'

interface Props {
  bars: Bar[]
  annotations?: Annotation[]
  ariaLabel?: string
}

export function CandlestickChart({ bars, annotations, ariaLabel }: Props): JSX.Element {
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

  const markers = useMemo(() => annotationsToMarkers(annotations ?? []), [annotations])

  useEffect(() => {
    const series = seriesRef.current
    if (!series) return
    series.setMarkers(markers)
  }, [markers])

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

/**
 * Map annotations to lightweight-charts series markers. Bullish goes
 * below the bar with an up-arrow; bearish goes above with a down-arrow.
 * Labels are truncated to ~MARKER_LABEL_MAX chars so a runaway agent
 * can't push a 5KB string into the chart tooltip layer.
 *
 * Returned markers are sorted ascending by time — lightweight-charts
 * requires this and will throw on out-of-order markers.
 *
 * Exported for direct unit testing of the kind->shape mapping.
 */
export function annotationsToMarkers(annotations: Annotation[]): SeriesMarker<UTCTimestamp>[] {
  return annotations
    .map((a) => {
      const time = Math.floor(new Date(a.event_ts).getTime() / 1000) as UTCTimestamp
      const text = a.label ? truncateLabel(a.label) : ''
      if (a.kind === 'bullish_marker') {
        return {
          time,
          position: 'belowBar' as const,
          shape: 'arrowUp' as const,
          color: BULLISH_COLOR,
          text,
        }
      }
      return {
        time,
        position: 'aboveBar' as const,
        shape: 'arrowDown' as const,
        color: BEARISH_COLOR,
        text,
      }
    })
    .sort((a, b) => (a.time as number) - (b.time as number))
}

function truncateLabel(label: string): string {
  return label.length <= MARKER_LABEL_MAX ? label : `${label.slice(0, MARKER_LABEL_MAX - 1)}…`
}
