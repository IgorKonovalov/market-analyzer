/**
 * Agent-mode toggle pinned to the chart header (Plan 0014, ADR-0021). Default
 * OFF — the user opts in to having their chart gestures (range-select, bar
 * click) forwarded to the agent. Controlled: the parent owns the state via
 * `useAgentMode`, so the chart and the toggle share one source of truth.
 *
 * Rendered as an ARIA switch (`role="switch"` + `aria-checked`) so it reads as
 * a toggle to assistive tech, not a plain button.
 */
import styles from './AgentModeToggle.module.css'

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
