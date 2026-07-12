/**
 * Chart layers legend (Plan 0047 phase 9). Lists every active layer — indicator
 * overlays, pattern-marker groups, and price lines (S/R) — each with a colour
 * swatch matching the drawn colour, the layer label, and a per-item checkbox that
 * shows/hides that layer. Purely presentational + ephemeral: the chart owns the
 * visibility state and passes the resolved layer list down; no sidecar/IPC/schema
 * touch.
 */
import { useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent } from 'react'

import { AddOverlayForm } from './AddOverlayForm'
import { GlossaryTerm } from './GlossaryTerm'
import { t } from '../lib/i18n'
import type { OverlaySpec } from '../types/events'
import styles from './LayersPanel.module.css'

// Draggable panel width (Plan 0071 follow-up). Persisted per the ADR-0039
// renderer-owned-prefs convention (`ma.*` localStorage key), read on mount,
// written on drag/keyboard end. Clamped so it can't collapse to nothing or eat
// the whole chart.
const PANEL_WIDTH_KEY = 'ma.layersPanelWidth'
const DEFAULT_PANEL_WIDTH = 240
const MIN_PANEL_WIDTH = 150
const MAX_PANEL_WIDTH = 600
const RESIZE_STEP = 16

function clampWidth(w: number): number {
  return Math.min(MAX_PANEL_WIDTH, Math.max(MIN_PANEL_WIDTH, Math.round(w)))
}

function readStoredWidth(): number {
  try {
    const raw = window.localStorage.getItem(PANEL_WIDTH_KEY)
    if (raw === null) return DEFAULT_PANEL_WIDTH
    const n = Number(raw)
    return Number.isFinite(n) ? clampWidth(n) : DEFAULT_PANEL_WIDTH
  } catch {
    return DEFAULT_PANEL_WIDTH
  }
}

function persistWidth(w: number): void {
  try {
    window.localStorage.setItem(PANEL_WIDTH_KEY, String(w))
  } catch {
    // Storage blocked (private mode / disabled) — the width just won't persist.
  }
}

export interface ChartLayer {
  /** Stable id: `overlay:ema:20` | `marker:bullish` | `pline:<label>` |
   * `trendlines:<pattern>|<style>` | `series:obv`. */
  id: string
  label: string
  /** Resolved colour — equals the colour the chart drew the layer with. */
  color: string
  kind: 'overlay' | 'marker' | 'price_line' | 'span' | 'trendline' | 'series'
  /** Per-item toggle; defaults true; never persisted. */
  visible: boolean
  /** Glossary key for an on-hover definition — set on indicator overlays (the
   * overlay kind: 'ema' / 'sma' / 'supertrend', Plan 0065) and on candlestick
   * marker group rows (the pattern token, Plan 0085). Absent for price-lines /
   * spans / trendlines / patternless groups, which render a plain label. */
  glossaryKey?: string
  /** Instance count for a grouped row (Plan 0067 phase 3): the number of lines
   * in a trendline (pattern type, state) group. Absent on ungrouped rows. */
  count?: number
  /** Highlight key for a trendline group (Plan 0067 phase 3): hovering the row
   * emphasises this group's lines on the chart. Absent on non-highlightable rows. */
  highlightKey?: string
  /** User-originated overlay (Plan 0082 phase 4, ADR-0077): the row gains a remove
   * control (the user owns it). Agent overlays are hide-only (removable falsy). */
  removable?: boolean
}

export interface LayersPanelProps {
  layers: ChartLayer[]
  onToggle: (id: string) => void
  /** Hover-to-highlight callback (Plan 0067 phase 3): fired with a trendline
   * group's `highlightKey` on row enter and `null` on leave. Optional — rows
   * without a `highlightKey` never call it. */
  onHighlight?: (key: string | null) => void
  /** Add a user overlay (Plan 0082 phase 4, ADR-0077). When provided, the panel
   * shows a `+ Indicator` form (and renders even with no layers yet, so the user
   * can add the first one). Absent when the chart has no (symbol, timeframe). */
  onAddOverlay?: (spec: OverlaySpec) => void
  /** Remove a user overlay by its layer id (Plan 0082 phase 4). Wired to the
   * remove control on `removable` rows; agent rows never call it. */
  onRemove?: (id: string) => void
}

export function LayersPanel({
  layers,
  onToggle,
  onHighlight,
  onAddOverlay,
  onRemove,
}: LayersPanelProps): JSX.Element | null {
  // Whether the add-indicator form is expanded (Plan 0082 phase 4).
  const [showForm, setShowForm] = useState(false)
  // The panel's user-set width (draggable via the left-edge handle). Held in
  // state so a drag re-renders live; mirrored in a ref so the drag-end persist
  // reads the final value without a stale closure.
  const [width, setWidth] = useState<number>(readStoredWidth)
  const widthRef = useRef(width)
  widthRef.current = width
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null)

  const beginResize = (e: PointerEvent<HTMLDivElement>): void => {
    e.preventDefault()
    // Pointer capture keeps move/up on the handle even as the cursor leaves it;
    // jsdom (tests) doesn't implement it, so guard.
    try {
      e.currentTarget.setPointerCapture(e.pointerId)
    } catch {
      // no-op — capture is a nicety, the drag still works without it.
    }
    dragRef.current = { startX: e.clientX, startWidth: width }
  }
  const onResizeMove = (e: PointerEvent<HTMLDivElement>): void => {
    const drag = dragRef.current
    if (drag === null) return
    // The panel is docked to the RIGHT of the chart, so dragging the handle LEFT
    // (clientX decreasing) widens it.
    setWidth(clampWidth(drag.startWidth + (drag.startX - e.clientX)))
  }
  const endResize = (e: PointerEvent<HTMLDivElement>): void => {
    if (dragRef.current === null) return
    dragRef.current = null
    try {
      e.currentTarget.releasePointerCapture(e.pointerId)
    } catch {
      // no-op (see beginResize).
    }
    persistWidth(widthRef.current)
  }
  const onResizeKeyDown = (e: KeyboardEvent<HTMLDivElement>): void => {
    // Arrow keys nudge the width — the accessible splitter pattern. Left widens
    // (toward the chart), Right narrows, mirroring the drag direction.
    const next =
      e.key === 'ArrowLeft'
        ? clampWidth(width + RESIZE_STEP)
        : e.key === 'ArrowRight'
          ? clampWidth(width - RESIZE_STEP)
          : null
    if (next === null) return
    e.preventDefault()
    setWidth(next)
    persistWidth(next)
  }

  // Render when there is something to list OR the user can add an overlay (so the
  // form is reachable to add the first one). Hidden only when both are absent.
  if (layers.length === 0 && onAddOverlay === undefined) return null
  return (
    <aside
      className={styles.panel}
      style={{ width }}
      aria-label={t('layers.panelAriaLabel')}
      data-testid="layers-panel"
    >
      <div
        className={styles.resizeHandle}
        role="separator"
        aria-orientation="vertical"
        aria-label={t('layers.resizeAria')}
        aria-valuenow={width}
        aria-valuemin={MIN_PANEL_WIDTH}
        aria-valuemax={MAX_PANEL_WIDTH}
        tabIndex={0}
        data-testid="layers-resize-handle"
        onPointerDown={beginResize}
        onPointerMove={onResizeMove}
        onPointerUp={endResize}
        onKeyDown={onResizeKeyDown}
      />
      <h2 className={styles.heading}>{t('layers.heading')}</h2>
      {onAddOverlay !== undefined && (
        <div className={styles.addSection}>
          <button
            type="button"
            className={styles.addToggle}
            onClick={() => setShowForm((v) => !v)}
            aria-expanded={showForm}
            data-testid="add-overlay-toggle"
          >
            {t('layers.addIndicator')}
          </button>
          {showForm && <AddOverlayForm onAdd={onAddOverlay} />}
        </div>
      )}
      <ul className={styles.list}>
        {layers.map((layer) => (
          <li
            key={layer.id}
            className={styles.row}
            data-testid={`layer-row:${layer.id}`}
            onMouseEnter={
              layer.highlightKey !== undefined
                ? () => onHighlight?.(layer.highlightKey ?? null)
                : undefined
            }
            onMouseLeave={layer.highlightKey !== undefined ? () => onHighlight?.(null) : undefined}
          >
            <label className={styles.label}>
              <input
                type="checkbox"
                className={styles.checkbox}
                checked={layer.visible}
                onChange={() => onToggle(layer.id)}
                aria-label={t('layers.toggleAria', { layerName: layer.label })}
              />
              <span
                className={styles.swatch}
                data-testid={`layer-swatch:${layer.id}`}
                style={{ backgroundColor: layer.color }}
                aria-hidden="true"
              />
              <span className={styles.layerLabel}>
                {layer.glossaryKey ? (
                  <GlossaryTerm termKey={layer.glossaryKey}>{layer.label}</GlossaryTerm>
                ) : (
                  layer.label
                )}
              </span>
              {layer.count !== undefined && (
                <span className={styles.count} data-testid={`layer-count:${layer.id}`}>
                  {layer.count}
                </span>
              )}
            </label>
            {layer.removable === true && onRemove !== undefined && (
              <button
                type="button"
                className={styles.removeButton}
                onClick={() => onRemove(layer.id)}
                aria-label={t('layers.removeAria', { layerName: layer.label })}
                data-testid={`layer-remove:${layer.id}`}
              >
                ×
              </button>
            )}
          </li>
        ))}
      </ul>
    </aside>
  )
}
