/**
 * Add-indicator form logic (Plan 0082 phase 4, ADR-0077).
 *
 * The kinds the `+ Indicator` form offers and a pure validator that turns the raw
 * form inputs into a clean `OverlaySpec` (or reports which field is invalid).
 * Kept out of the component so it is unit-testable without a DOM.
 *
 * Scope: the client-computable overlay-path kinds whose render landed by phase 2
 * (ema/sma/bbands/supertrend/ichimoku) — the same set the user-overlay store
 * accepts. VWAP and OBV are always-on aggregate series drawn on their own scales,
 * not overlay-path adds, so they are NOT offered here (followups — each needs its
 * own OverlayKind + draw path, deferred by the plan).
 */
import type { OverlayKind, OverlaySpec } from '../types/events'

/** The indicator kinds the add-indicator dropdown offers, in display order. A
 * literal tuple (not `OverlayKind[]`) so a `layers.kind.<k>` message key stays a
 * union of exactly these five — the locale catalog need not carry the others. */
export const OVERLAY_FORM_KINDS = [
  'ema',
  'sma',
  'bbands',
  'supertrend',
  'ichimoku',
  // Plan 0091 momentum oscillators — each drawn in its own sub-pane, fieldless in
  // v1 (classic default periods applied by the renderer, like ichimoku).
  'stochastic',
  'stoch_rsi',
  'cci',
  'williams_r',
  'roc',
  'mfi',
  'cmf',
  'ad_line',
  // Plan 0091 phase 9: RSI + MACD-histogram, promoted to first-class oscillator
  // sub-panes (so divergence segments have a pane to draw on) and thus addable /
  // toggleable like the rest. Fieldless in v1 (classic default periods).
  'rsi',
  'macd',
  // Plan 0092: price-structure geometry overlays. Fieldless in the form — the
  // renderer auto-anchors (dominant swing / last bar), retracement + floor
  // defaults; the agent overrides fib_kind / method / anchors via show_chart.
  'fibonacci',
  'pivot_points',
  'anchored_vwap',
] as const satisfies readonly OverlayKind[]

export type OverlayFormKind = (typeof OVERLAY_FORM_KINDS)[number]

/** Kinds that take a user-supplied period. Ichimoku uses its classic 9/26/52/26
 * defaults in v1 (full parameterisation is a followup), so it takes none. */
const PERIOD_KINDS: ReadonlySet<OverlayKind> = new Set(['ema', 'sma', 'bbands', 'supertrend'])

/** Whether the form shows a period input for this kind. */
export function formKindTakesPeriod(kind: OverlayKind): boolean {
  return PERIOD_KINDS.has(kind)
}

/** Whether the form shows a std-dev (`k`) input for this kind — Bollinger only. */
export function formKindTakesStdDev(kind: OverlayKind): boolean {
  return kind === 'bbands'
}

/** A default period per kind, for the form's initial value on a kind change. */
export function defaultPeriodFor(kind: OverlayKind): number {
  switch (kind) {
    case 'sma':
      return 50
    case 'supertrend':
      return 10
    default:
      return 20
  }
}

export type OverlayFormResult =
  | { ok: true; spec: OverlaySpec }
  | { ok: false; error: 'period' | 'stdDev' }

/**
 * Validate the raw form inputs and build a clean `OverlaySpec`, or report which
 * field is invalid. `period` must be a positive integer for the period kinds;
 * `k` (the std-dev multiplier, stored on `multiplier`) must be > 0 for bbands.
 * Ichimoku takes neither (classic defaults). Never emits `price`/`label`/`role`.
 */
export function buildOverlayFromForm(
  kind: OverlayKind,
  period: number,
  k: number,
): OverlayFormResult {
  const spec: OverlaySpec = { kind }
  if (formKindTakesPeriod(kind)) {
    if (!Number.isInteger(period) || period <= 0) return { ok: false, error: 'period' }
    spec.period = period
  }
  if (formKindTakesStdDev(kind)) {
    if (!Number.isFinite(k) || k <= 0) return { ok: false, error: 'stdDev' }
    spec.multiplier = k
  }
  return { ok: true, spec }
}
