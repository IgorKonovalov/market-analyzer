/**
 * Position-box maths for the drawing dock (Plan 0104, ADR-0099).
 *
 * The SINGLE home of the entry/stop/target ordering invariant — long needs
 * `stop < entry < target`, short needs `target < entry < stop` — deliberately
 * pure and separate from the pointer machinery so the invariant is unit-tested on
 * its own (the plan's "one clamp function" risk mitigation). Risk-reward is DERIVED
 * here, never stored on the `DrawingSpec` (ADR-0099).
 */
import type { DrawingKind, DrawingSpec, TimePricePoint } from '../types/events'

export const POSITION_KINDS = ['long_position', 'short_position'] as const
export type PositionKind = (typeof POSITION_KINDS)[number]

const POSITION_KIND_SET: ReadonlySet<string> = new Set(POSITION_KINDS)

export function isPositionKind(kind: DrawingKind): kind is PositionKind {
  return POSITION_KIND_SET.has(kind)
}

/** Fraction of the entry price used for the default risk leg (1%); the target is
 * twice that, so a fresh position defaults to a 2:1 reward:risk. */
const DEFAULT_RISK_FRAC = 0.01
/** Absolute floor so a near-zero entry price still yields a non-degenerate gap. */
const EPS_FLOOR = 1e-9

/** The smallest gap kept between entry and a level so the strict ordering holds
 * without a visibly-degenerate zone: 0.01% of the entry price, floored. */
function ordEps(entry: number): number {
  return Math.max(Math.abs(entry) * 1e-4, EPS_FLOOR)
}

/**
 * Default 2:1 reward:risk levels straddling `entry` for a fresh position: a long
 * risks 1% below and targets 2% above; a short mirrors it. A single placement
 * click sets the entry to the cursor and these defaults; the user then drags the
 * three handles.
 */
export function defaultPositionLevels(
  kind: PositionKind,
  entry: number,
): { stop: number; target: number } {
  const risk = Math.max(Math.abs(entry) * DEFAULT_RISK_FRAC, EPS_FLOOR)
  if (kind === 'long_position') {
    return { stop: entry - risk, target: entry + 2 * risk }
  }
  return { stop: entry + risk, target: entry - 2 * risk }
}

/**
 * Derived risk-reward = `|target − entry| / |entry − stop|` (Plan 0104, never
 * stored). Returns `null` when the risk leg is degenerate (`entry === stop`).
 */
export function riskReward(entry: number, stop: number, target: number): number | null {
  const risk = Math.abs(entry - stop)
  if (risk === 0) return null
  return Math.abs(target - entry) / risk
}

/**
 * Enforce the position ordering invariant (Plan 0104 / ADR-0099). Given desired
 * (post-drag) levels, clamp `stop`/`target` to the correct side of `entry` — a stop
 * dragged through the entry is pinned just beyond it rather than flipping the
 * position. Pure; the single home of the invariant.
 */
export function enforcePositionOrder(
  kind: PositionKind,
  entry: number,
  stop: number,
  target: number,
): { entry: number; stop: number; target: number } {
  const eps = ordEps(entry)
  if (kind === 'long_position') {
    return { entry, stop: Math.min(stop, entry - eps), target: Math.max(target, entry + eps) }
  }
  return { entry, stop: Math.max(stop, entry + eps), target: Math.min(target, entry - eps) }
}

/**
 * Whether a stored/parsed position's `stop`/`target` are finite and satisfy the
 * strict ordering for its kind — the persistence sanitizer drops a position that
 * fails (never renders an invalid box).
 */
export function positionLevelsValid(
  kind: PositionKind,
  entry: number,
  stop: number | null | undefined,
  target: number | null | undefined,
): boolean {
  if (typeof stop !== 'number' || !Number.isFinite(stop)) return false
  if (typeof target !== 'number' || !Number.isFinite(target)) return false
  if (kind === 'long_position') return stop < entry && entry < target
  return target < entry && entry < stop
}

/**
 * Apply a handle drag to a position spec (handle 0 = entry, 1 = stop, 2 = target),
 * re-enforcing the ordering invariant. Entry drags move the anchor in time+price;
 * stop/target drags move only that price level. Returns the spec unchanged for a
 * non-position kind (defensive) or an out-of-range handle.
 */
export function applyPositionHandleDrag(
  spec: DrawingSpec,
  handleIndex: number,
  anchor: TimePricePoint,
): DrawingSpec {
  if (!isPositionKind(spec.kind)) return spec
  const entry0 = spec.points[0].price
  const stop0 = spec.stop ?? entry0
  const target0 = spec.target ?? entry0
  let entry = entry0
  let stop = stop0
  let target = target0
  let points = spec.points
  if (handleIndex === 0) {
    entry = anchor.price
    points = [anchor]
  } else if (handleIndex === 1) {
    stop = anchor.price
  } else if (handleIndex === 2) {
    target = anchor.price
  } else {
    return spec
  }
  const ordered = enforcePositionOrder(spec.kind, entry, stop, target)
  return { ...spec, points, stop: ordered.stop, target: ordered.target }
}
