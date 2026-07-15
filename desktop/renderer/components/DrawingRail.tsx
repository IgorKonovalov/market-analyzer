/**
 * The left-edge drawing dock's tool rail (Plan 0097 phase 2, ADR-0091).
 *
 * Presentational: a vertical column of tool buttons (select + the drawing kinds)
 * and a delete affordance. All state + behaviour live in `useDrawingTools`; this
 * only renders the buttons and reports clicks. Fills the left-edge slot Plan 0096
 * reserved in `CandlestickChart`. Plan 0097 ships select + the six geometry kinds;
 * Plan 0104 adds the two position boxes and the three range measures.
 */
import type { DrawingKind, DrawingProvenance } from '../types/events'
import { t } from '../lib/i18n'
import styles from './DrawingRail.module.css'

interface DrawingRailProps {
  activeTool: DrawingKind | null
  onSelectTool: (tool: DrawingKind | null) => void
  onDelete: () => void
  hasSelection: boolean
  /** Provenance of the selection (Plan 0097 phase 4): an agent drawing is
   * hide-only, so the affordance reads "hide" rather than "delete". */
  selectedProvenance?: DrawingProvenance | null
  /** Disabled until the chart carries a symbol to key drawings by. */
  disabled?: boolean
}

interface ToolButton {
  tool: DrawingKind
  glyph: string
  labelKey: string
}

const TOOL_BUTTONS: ToolButton[] = [
  { tool: 'trendline', glyph: '╱', labelKey: 'chart.draw.trendline' },
  { tool: 'ray', glyph: '⟶', labelKey: 'chart.draw.ray' },
  { tool: 'hline', glyph: '─', labelKey: 'chart.draw.hline' },
  { tool: 'vline', glyph: '│', labelKey: 'chart.draw.vline' },
  { tool: 'rect', glyph: '▭', labelKey: 'chart.draw.rect' },
  { tool: 'fib', glyph: '≣', labelKey: 'chart.draw.fib' },
  // Plan 0104 trading-idea tools: position boxes + range measures.
  { tool: 'long_position', glyph: '⬆', labelKey: 'chart.draw.long_position' },
  { tool: 'short_position', glyph: '⬇', labelKey: 'chart.draw.short_position' },
  { tool: 'date_range', glyph: '↔', labelKey: 'chart.draw.date_range' },
  { tool: 'price_range', glyph: '↕', labelKey: 'chart.draw.price_range' },
  { tool: 'date_price_range', glyph: '▦', labelKey: 'chart.draw.date_price_range' },
]

export function DrawingRail({
  activeTool,
  onSelectTool,
  onDelete,
  hasSelection,
  selectedProvenance = null,
  disabled = false,
}: DrawingRailProps): JSX.Element {
  // An agent drawing is hide-only; the same button then reads "hide".
  const isAgentSelected = selectedProvenance === 'agent'
  const deleteLabel = isAgentSelected ? t('chart.draw.hide') : t('chart.draw.delete')
  return (
    <div
      className={styles.rail}
      role="toolbar"
      aria-orientation="vertical"
      aria-label={t('chart.draw.railLabel')}
      data-testid="drawing-rail"
    >
      <button
        type="button"
        className={`${styles.toolButton} ${styles.selectButton}`}
        aria-pressed={activeTool === null}
        aria-label={t('chart.draw.select')}
        title={t('chart.draw.select')}
        disabled={disabled}
        data-testid="drawing-tool-select"
        onClick={() => onSelectTool(null)}
      >
        <span aria-hidden="true">⌖</span>
      </button>
      {TOOL_BUTTONS.map(({ tool, glyph, labelKey }) => (
        <button
          key={tool}
          type="button"
          className={styles.toolButton}
          aria-pressed={activeTool === tool}
          aria-label={t(labelKey)}
          title={t(labelKey)}
          disabled={disabled}
          data-testid={`drawing-tool-${tool}`}
          onClick={() => onSelectTool(tool)}
        >
          <span aria-hidden="true">{glyph}</span>
        </button>
      ))}
      <button
        type="button"
        className={styles.deleteButton}
        aria-label={deleteLabel}
        title={deleteLabel}
        disabled={disabled || !hasSelection}
        data-testid="drawing-tool-delete"
        onClick={onDelete}
      >
        <span aria-hidden="true">{isAgentSelected ? '🙈' : '🗑'}</span>
      </button>
    </div>
  )
}
