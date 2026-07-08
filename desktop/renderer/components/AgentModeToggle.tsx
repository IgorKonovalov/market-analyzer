/**
 * Agent-mode toggle pinned to the chart header (Plan 0014, ADR-0021). Default
 * OFF — the user opts in to having their chart gestures (range-select, bar
 * click) forwarded to the agent. Controlled: the parent owns the state via
 * `useAgentMode`, so the chart and the toggle share one source of truth.
 *
 * Rendered as an ARIA switch (`role="switch"` + `aria-checked`) so it reads as
 * a toggle to assistive tech, not a plain button.
 *
 * The mental model is easy to get wrong (Plan 0064 phase 6): agent mode gates
 * only pointer-GESTURE forwarding, NOT overlay visibility. Agent-drawn overlays
 * — markers, trendlines, price lines — always render regardless of the toggle.
 * That's why a fresh `detect_chart_patterns` draws lines even with agent mode
 * OFF. The clarifying copy lives in `title` (surfaced as the switch's accessible
 * description); no behaviour changes with it.
 */
import styles from './AgentModeToggle.module.css'

/** Tooltip + accessible description: what agent mode does and, crucially, what
 * it does NOT do (Plan 0064 phase 6 — the markers-drew-but-lines-didn't
 * confusion was a mental-model gap, not a bug). */
export const AGENT_MODE_HELP =
  'Agent mode controls gesture forwarding only — whether your chart gestures ' +
  '(range-select, bar-click) are sent to the agent. It does not affect overlay ' +
  'visibility: agent-drawn overlays (markers, trendlines, price lines) always ' +
  'render regardless of agent mode.'

interface Props {
  enabled: boolean
  setEnabled: (next: boolean) => void
  disabled?: boolean
}

export function AgentModeToggle({ enabled, setEnabled, disabled = false }: Props): JSX.Element {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      aria-label="Toggle agent mode"
      title={AGENT_MODE_HELP}
      data-testid="agent-mode-toggle"
      data-region="chart-header-right"
      className={styles.toggle}
      data-enabled={enabled}
      disabled={disabled}
      onClick={() => setEnabled(!enabled)}
    >
      <span className={styles.track} aria-hidden="true">
        <span className={styles.thumb} />
      </span>
      <span className={styles.label}>Agent mode {enabled ? 'ON' : 'OFF'}</span>
    </button>
  )
}
