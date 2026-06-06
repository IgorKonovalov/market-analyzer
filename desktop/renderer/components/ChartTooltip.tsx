/**
 * Floating hover tooltip for the candlestick chart (Plan 0047 phase 8). Pure
 * presentational: it renders the `TooltipContent` the chart computed from the
 * crosshair and positions itself at the crosshair point. Ephemeral — the chart
 * mounts it only while hovering and unmounts it on move-away.
 */
import type { TooltipContent } from '../lib/tooltip'
import styles from './ChartTooltip.module.css'

export interface ChartTooltipProps {
  content: TooltipContent
  /** Crosshair point within the chart container, in px. */
  x: number
  y: number
}

export function ChartTooltip({ content, x, y }: ChartTooltipProps): JSX.Element {
  return (
    <div
      className={styles.tooltip}
      data-testid="chart-tooltip"
      role="tooltip"
      style={{ left: x, top: y }}
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
