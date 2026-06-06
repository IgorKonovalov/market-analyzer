/**
 * Floating hover tooltip for the candlestick chart (Plan 0047 phase 8). Pure
 * presentational: it renders the `TooltipContent` the chart computed from the
 * crosshair and positions itself at the crosshair point. Ephemeral — the chart
 * mounts it only while hovering and unmounts it on move-away.
 */
import { useLayoutEffect, useRef, useState } from 'react'

import { type TooltipContent, tooltipPosition } from '../lib/tooltip'
import styles from './ChartTooltip.module.css'

export interface ChartTooltipProps {
  content: TooltipContent
  /** Crosshair point within the chart container, in px. */
  x: number
  y: number
  /** Chart container size, so the tooltip can flip/clamp to stay on-screen. */
  containerWidth: number
  containerHeight: number
}

export function ChartTooltip({
  content,
  x,
  y,
  containerWidth,
  containerHeight,
}: ChartTooltipProps): JSX.Element {
  const ref = useRef<HTMLDivElement>(null)
  // Measure the tooltip's own size after render so the placement can flip near an
  // edge. Starts at 0 (down-right placement on first paint), then settles once
  // measured — re-measured whenever the content or crosshair moves.
  const [size, setSize] = useState({ width: 0, height: 0 })
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const { offsetWidth, offsetHeight } = el
    setSize((prev) =>
      prev.width === offsetWidth && prev.height === offsetHeight
        ? prev
        : { width: offsetWidth, height: offsetHeight },
    )
  }, [content, x, y])

  const { left, top } = tooltipPosition({
    x,
    y,
    width: size.width,
    height: size.height,
    containerWidth,
    containerHeight,
  })

  return (
    <div
      ref={ref}
      className={styles.tooltip}
      data-testid="chart-tooltip"
      role="tooltip"
      style={{ left, top }}
    >
      {content.markers.map((label, i) => (
        <div key={`marker-${i}`} className={styles.marker} data-testid="tooltip-marker">
          {label}
        </div>
      ))}
      {content.overlays.map((reading, i) => (
        <div key={`overlay-${i}`} className={styles.overlay} data-testid="tooltip-overlay">
          <span className={styles.overlayLabel}>{reading.label}</span>
          <span className={styles.overlayValue}>{reading.value.toFixed(2)}</span>
        </div>
      ))}
    </div>
  )
}
