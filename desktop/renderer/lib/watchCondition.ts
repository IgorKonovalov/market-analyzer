/**
 * Condition summary for a watch definition (Plan 0110 phase 3).
 *
 * `WatchOut.params` is an opaque `Record<string, unknown>` on the wire (the
 * sidecar validates it against the kind's model; the renderer only displays
 * it), so every field is read defensively. Unknown kinds — or params missing
 * the fields a kind needs — fall back to the kind slug rather than throwing:
 * future watch kinds render as their slug until this formatter learns them.
 *
 * The summary is a condition FACT string (ADR-0029); the free-text `note` is
 * user/agent context and must never be interpolated here.
 */
import { t } from './i18n'

const OPERATOR_GLYPHS: Record<string, string> = {
  '<': '<',
  '<=': '≤',
  '>': '>',
  '>=': '≥',
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null
}

export function formatWatchCondition(kind: string, params: Record<string, unknown>): string {
  if (kind === 'indicator_threshold') {
    const indicator = asString(params.indicator)
    const operator = asString(params.operator)
    const level =
      typeof params.level === 'number' && Number.isFinite(params.level) ? params.level : null
    if (indicator !== null && operator !== null && level !== null) {
      return `${indicator} ${OPERATOR_GLYPHS[operator] ?? operator} ${String(level)}`
    }
  }
  if (kind === 'pattern') {
    const pattern = asString(params.pattern)
    if (pattern !== null) {
      // Reuse the chart glossary's localized pattern names; an unknown pattern
      // (t() returns the key itself) falls back to the raw slug.
      const label = t(`enum.pattern.${pattern}`)
      return label === `enum.pattern.${pattern}` ? pattern : label
    }
  }
  if (kind === 'strategy_signal') {
    const strategyId = asString(params.strategy_id)
    if (strategyId !== null) return strategyId
  }
  return kind
}
