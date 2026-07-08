/**
 * Chart layers legend (Plan 0047 phase 9). Lists every active layer — indicator
 * overlays, pattern-marker groups, and price lines (S/R) — each with a colour
 * swatch matching the drawn colour, the layer label, and a per-item checkbox that
 * shows/hides that layer. Purely presentational + ephemeral: the chart owns the
 * visibility state and passes the resolved layer list down; no sidecar/IPC/schema
 * touch.
 */
import { GlossaryTerm } from './GlossaryTerm'
import styles from './LayersPanel.module.css'

export interface ChartLayer {
  /** Stable id: `overlay:ema:20` | `marker:bullish` | `pline:<label>`. */
  id: string
  label: string
  /** Resolved colour — equals the colour the chart drew the layer with. */
  color: string
  kind: 'overlay' | 'marker' | 'price_line' | 'span' | 'trendline'
  /** Per-item toggle; defaults true; never persisted. */
  visible: boolean
  /** Glossary key for an on-hover definition (Plan 0065) — set on indicator
   * overlays (the overlay kind: 'ema' / 'sma' / 'supertrend'). Absent for
   * markers / price-lines / spans / trendlines, which render a plain label. */
  glossaryKey?: string
}

export interface LayersPanelProps {
  layers: ChartLayer[]
  onToggle: (id: string) => void
}

export function LayersPanel({ layers, onToggle }: LayersPanelProps): JSX.Element | null {
  if (layers.length === 0) return null
  return (
    <aside className={styles.panel} aria-label="Chart layers" data-testid="layers-panel">
      <h2 className={styles.heading}>Layers</h2>
      <ul className={styles.list}>
        {layers.map((layer) => (
          <li key={layer.id} className={styles.row} data-testid={`layer-row:${layer.id}`}>
            <label className={styles.label}>
              <input
                type="checkbox"
                className={styles.checkbox}
                checked={layer.visible}
                onChange={() => onToggle(layer.id)}
                aria-label={`Toggle ${layer.label}`}
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
            </label>
          </li>
        ))}
      </ul>
    </aside>
  )
}
