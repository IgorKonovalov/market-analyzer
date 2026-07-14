/**
 * The left-edge drawing dock's tool rail (Plan 0097 phase 2, ADR-0091).
 *
 * Presentational: a vertical column of tool buttons (select + the drawing kinds)
 * and a delete affordance. All state + behaviour live in `useDrawingTools`; this
 * only renders the buttons and reports clicks. Fills the left-edge slot Plan 0096
 * reserved in `CandlestickChart`. Phase 2 ships select + trendline + ray; phase 3
 * adds hline / vline / rect / fib buttons here.
 */
import type { DrawingKind } from '../types/events'
import { t } from '../lib/i18n'
import styles from './DrawingRail.module.css'

interface DrawingRailProps {
  activeTool: DrawingKind | null
  onSelectTool: (tool: DrawingKind | null) => void
  onDelete: () => void
  hasSelection: boolean
  /** Disabled until the chart carries a symbol to key drawings by. */
  disabled?: boolean
}

interface ToolButton {
  tool: DrawingKind
  glyph: string
  labelKey: string
}

// Phase 2 tools; phase 3 appends hline / vline / rect / fib.
const TOOL_BUTTONS: ToolButton[] = [
  { tool: 'trendline', glyph: '╱', labelKey: 'chart.draw.trendline' },
  { tool: 'ray', glyph: '⟶', labelKey: 'chart.draw.ray' },
]

export function DrawingRail({
  activeTool,
  onSelectTool,
  onDelete,
  hasSelection,
  disabled = false,
}: DrawingRailProps): JSX.Element {
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
        className={styles.toolButton}
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
        aria-label={t('chart.draw.delete')}
        title={t('chart.draw.delete')}
        disabled={disabled || !hasSelection}
        data-testid="drawing-tool-delete"
        onClick={onDelete}
      >
        <span aria-hidden="true">🗑</span>
      </button>
    </div>
  )
}
