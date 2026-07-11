/**
 * Reason-code localization (Plan 0069 phase 5, ADR-0063).
 *
 * The sidecar stays English-authoritative and ships, beside its English prose,
 * structured `{code, params}` reason-codes (advisor `fusion.py` — rationale,
 * blockers, gate-checks, condition/signal facts; forecast `explain.py` — the
 * disclaimer / no-scored-folds note). This module turns one reason-code into a
 * localized string: it maps the closed-vocabulary enum values that ride as raw
 * tokens in `params` through the renderer's enum-label catalog, formats numeric
 * params `en-US` (numbers stay `en-US` by decision — ADR-0063), then resolves
 * the code's template via `t()`.
 *
 * The sidecar never parses prose to translate it (ADR-0063's "the sidecar ships
 * facts, the renderer owns wording") — every translatable enum is a closed set,
 * so the token → label mapping is a lookup, not a parse.
 */
import { t } from './i18n'
import type { Params } from './i18n'
import type { ReasonCode } from '../types/events'

/**
 * Localize one closed-vocabulary enum token to its catalog label. The key is
 * `enum.<group>.<token>` (token lower-cased, whitespace → `_`, so the upstream
 * Fear & Greed `"Extreme Fear"` becomes `enum.fear_greed.extreme_fear`). A
 * token with no catalog entry — a new sidecar enum member that landed without a
 * matching label — falls back to the raw token, never the dotted key (the
 * Plan 0069 phase 4b risk-note contract; the phase-7 smoke surfaces it).
 */
export function enumLabel(group: string, token: string): string {
  const key = `enum.${group}.${normalizeToken(token)}`
  const label = t(key)
  return label === key ? token : label
}

function normalizeToken(token: string): string {
  return token.trim().toLowerCase().replace(/\s+/g, '_')
}

/** Numeric reason-code params format `en-US` (ADR-0063): integers bare, other
 * numbers to at most three fraction digits. The renderer — not the sidecar —
 * owns display precision, so a raw `0.6` reads as `0.6`, not `0.600`. */
const REASON_NUMBER_FORMAT = new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 })

function formatReasonNumber(value: number): string {
  return Number.isInteger(value) ? String(value) : REASON_NUMBER_FORMAT.format(value)
}

/**
 * Which params of which codes carry a closed-vocabulary enum token, and the
 * enum-label group each maps through. Everything not listed here is a plain
 * string (a strategy id, a joined id list) or a number — passed through or
 * `en-US`-formatted respectively. Keeping the map here, beside the templates it
 * feeds, is what keeps a raw enum word from leaking into a translated line
 * (the phase-5 "reason.conditions has no raw English enum word" criterion,
 * extended to every enum-bearing code).
 */
const ENUM_PARAM_GROUPS: Record<string, Record<string, string>> = {
  // Directional rationale (fusion.py).
  'reason.forecast': { direction: 'position', edge_strength: 'edge_strength' },
  'reason.signals_agree': { direction: 'position' },
  'reason.conditions': { trend: 'trend', momentum: 'momentum', volume: 'volume' },
  // Flat-verdict blockers (fusion.py).
  'blocker.signals_disagree_forecast': { signal_dir: 'position', forecast_dir: 'position' },
  // Non-voting inputs (fusion.py, Plan 0077 phase 5). vol_source and the regime
  // state ride as closed-vocab tokens mapped through the enum-label catalog.
  'reason.sizing': { vol_source: 'vol_source' },
  'reason.regime_context': { current_regime: 'regime' },
  // Condition/signal facts (fusion.py _build_basis, phase 4b).
  'condition.trend': { value: 'trend' },
  'condition.momentum': { value: 'momentum' },
  'condition.volume': { value: 'volume' },
  'condition.candlestick': { pattern: 'pattern', direction: 'direction' },
  'signal.vote': { position: 'position' },
}

/**
 * Localize one reason-code to a display string. Enum tokens map through the
 * enum-label catalog; numbers format `en-US`; the resolved `params` feed the
 * code's `t()` template.
 *
 * Two codes carry a detail clause the sidecar emits only when the value exists
 * (`reason.forecast`'s out-of-sample skill pair, `blocker.no_backtested_edge`'s
 * sharpe). A synthetic `_present` 0|1 flag drives an ICU-lite plural arm in the
 * template so the clause — and its `{param}` refs — vanish when the value is
 * absent, rather than leaking a literal `{skill}` for a missing param.
 */
export function localizeReasonCode(rc: ReasonCode): string {
  const groups = ENUM_PARAM_GROUPS[rc.code]
  const params: Params = {}
  for (const [key, value] of Object.entries(rc.params)) {
    if (groups?.[key] !== undefined && typeof value === 'string') {
      params[key] = enumLabel(groups[key], value)
    } else if (typeof value === 'number') {
      params[key] = formatReasonNumber(value)
    } else {
      params[key] = value
    }
  }

  if (rc.code === 'reason.forecast') {
    params._skill = 'skill' in rc.params ? 1 : 0
  } else if (rc.code === 'blocker.no_backtested_edge') {
    params._sharpe = 'sharpe_mean' in rc.params ? 1 : 0
  }

  return t(rc.code, params)
}
