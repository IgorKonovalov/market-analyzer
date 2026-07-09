/**
 * Chart layers legend (Plan 0047 phase 9). Lists every active layer — indicator
 * overlays, pattern-marker groups, and price lines (S/R) — each with a colour
 * swatch matching the drawn colour, the layer label, and a per-item checkbox that
 * shows/hides that layer. Purely presentational + ephemeral: the chart owns the
 * visibility state and passes the resolved layer list down; no sidecar/IPC/schema
 * touch.
 */
import { GlossaryTerm } from './GlossaryTerm'
import { t } from '../lib/i18n'
import styles from './LayersPanel.module.css'

export interface ChartLayer {
  /** Stable id: `overlay:ema:20` | `marker:bullish` | `pline:<label>` |
   * `trendlines:<pattern>|<style>`. */
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
  /** Instance count for a grouped row (Plan 0067 phase 3): the number of lines
   * in a trendline (pattern type, state) group. Absent on ungrouped rows. */
  count?: number
  /** Highlight key for a trendline group (Plan 0067 phase 3): hovering the row
   * emphasises this group's lines on the chart. Absent on non-highlightable rows. */
  highlightKey?: string
}

export interface LayersPanelProps {
  layers: ChartLayer[]
  onToggle: (id: string) => void
  /** Hover-to-highlight callback (Plan 0067 phase 3): fired with a trendline
   * group's `highlightKey` on row enter and `null` on leave. Optional — rows
   * without a `highlightKey` never call it. */
  onHighlight?: (key: string | null) => void
}

export function LayersPanel({
  layers,
  onToggle,
  onHighlight,
}: LayersPanelProps): JSX.Element | null {
  if (layers.length === 0) return null
  return (
    <aside
      className={styles.panel}
      aria-label={t('layers.panelAriaLabel')}
      data-testid="layers-panel"
    >
      <h2 className={styles.heading}>{t('layers.heading')}</h2>
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
          </li>
        ))}
      </ul>
    </aside>
  )
}
