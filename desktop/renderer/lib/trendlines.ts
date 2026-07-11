/**
 * Chart trendlines → a lightweight-charts series primitive that strokes sloped
 * polylines anchored at `(time, price)` points (Plan 0052 phase 4, ADR-0049): a
 * head-and-shoulders neckline, the bounding lines of a triangle/wedge. Modelled
 * on `spans.ts` (ADR-0045): the primitive rides the chart's own coordinate
 * system and tracks pan/zoom for free.
 *
 * Unlike the span band (time-only), a trendline needs the PRICE axis too, so the
 * pixel math maps each anchor through both the time axis and the candle series'
 * `priceToCoordinate`. `currentSegments()` returns `[]` until `attached()` has
 * run (the `currentRects`-empty-until-attached pattern).
 *
 * On the time axis, `timeScale().timeToCoordinate` is null-for-off-grid: it
 * resolves ONLY times that land exactly on a loaded bar, so a projected neckline
 * or a bound reaching past the last bar would be dropped (Plan 0064 phase 1 /
 * ADR-0059). `resolveTimeX` fixes that: on a `null` it interpolates/extrapolates
 * x from the loaded bar grid through the LOGICAL scale — matching how
 * `priceToCoordinate` extrapolates on the price axis — so an anchor only fails
 * to map on a genuinely empty/degenerate chart (< 2 bars).
 *
 * `style` is the forming-vs-confirmed cue: `dashed` = forming, `solid` =
 * confirmed. Colours resolve from the theme tokens by PATTERN TYPE (Plan 0067 /
 * ADR-0061, superseding ADR-0049's role→colour): every line of one pattern (a
 * triangle's two bounds, a head-and-shoulders' neckline) shares one categorical
 * hue, so same-coloured lines read as one shape; a roleless/unknown pattern
 * uses the neutral token. Role is still on the wire but no longer drives colour.
 * The redundant forming(dashed)+confirmed(solid) twin of one geometry is
 * collapsed by `dedupeTrendlines` before drawing (the caller applies it).
 *
 * The pixel math lives in the pure, canvas-free `computeTrendlineSegments` so
 * it is unit-testable; the renderer's `draw` only strokes the segments.
 */
import type {
  ISeriesPrimitive,
  ISeriesPrimitivePaneRenderer,
  ISeriesPrimitivePaneView,
  Logical,
  PrimitiveHoveredItem,
  SeriesAttachedParameter,
  SeriesPrimitivePaneViewZOrder,
  Time,
  UTCTimestamp,
} from 'lightweight-charts'

import type { TrendlineSpec } from '../types/events'

/** Single legend row id/label controlling ALL trendlines (Plan 0052 phase 4),
 * mirroring the span layer's `SPAN_LAYER_ID` pattern. */
export const TRENDLINE_LAYER_ID = 'trendlines'
export const TRENDLINE_LAYER_LABEL = 'Trendlines'

/** The classical chart-pattern types the detector emits (mirror of
 * `CHART_PATTERNS` in `analysis/chart_patterns.py`) — the categorical colour
 * key (Plan 0067 / ADR-0061). Order is the palette-token order in styles.css. */
export const TRENDLINE_PATTERN_TYPES = [
  'head_shoulders',
  'inverse_head_shoulders',
  'double_top',
  'double_bottom',
  'ascending_triangle',
  'descending_triangle',
  'symmetrical_triangle',
  'rising_wedge',
  'falling_wedge',
] as const
export type TrendlinePatternType = (typeof TRENDLINE_PATTERN_TYPES)[number]

/** Theme-resolved categorical palette: one distinct hue per pattern type, plus a
 * stable neutral for an unknown/absent pattern (Plan 0067 / ADR-0061). Resolved
 * from the DOM tokens by the caller and passed in — same discipline as before,
 * but keyed by pattern type rather than role. */
export type TrendlineColors = Record<TrendlinePatternType, string> & {
  /** Fallback hue for a roleless/unknown pattern. */
  neutral: string
}

// Light-theme fallbacks for when a token is unset — e.g. in jsdom unit tests
// where styles.css isn't loaded. At runtime the tokens in styles.css win. These
// mirror the `--trendline-*` light values in styles.css.
const TRENDLINE_COLOR_FALLBACK: TrendlineColors = {
  head_shoulders: '#e03131',
  inverse_head_shoulders: '#e8590c',
  double_top: '#ae3ec9',
  double_bottom: '#2f9e44',
  ascending_triangle: '#099268',
  descending_triangle: '#c2255c',
  symmetrical_triangle: '#1971c2',
  rising_wedge: '#6741d9',
  falling_wedge: '#0c8599',
  neutral: '#64748b',
}

/** The `--trendline-<pattern>` CSS-variable name for a pattern type
 * (underscores → hyphens), e.g. `head_shoulders` → `--trendline-head-shoulders`. */
function patternToken(type: TrendlinePatternType): string {
  return `--trendline-${type.replaceAll('_', '-')}`
}

/** Resolve the categorical trendline palette off the themed DOM (lightweight-
 * charts/canvas can't resolve `var(--x)` strings), falling back to the light
 * defaults. Reads one `--trendline-<pattern>` token per type plus the neutral. */
export function readTrendlineColors(el: HTMLElement): TrendlineColors {
  const c = getComputedStyle(el)
  const v = (name: string, fallback: string): string => c.getPropertyValue(name).trim() || fallback
  const colors = {
    neutral: v('--marker-neutral', TRENDLINE_COLOR_FALLBACK.neutral),
  } as TrendlineColors
  for (const type of TRENDLINE_PATTERN_TYPES) {
    colors[type] = v(patternToken(type), TRENDLINE_COLOR_FALLBACK[type])
  }
  return colors
}

/** Pattern type → theme token. Exported for the legend swatch (the row's colour
 * must equal the colour the lines are drawn with). An unknown/absent pattern
 * falls back to the stable neutral hue. */
export function trendlineColor(
  pattern: string | null | undefined,
  colors: TrendlineColors,
): string {
  if (pattern != null && (TRENDLINE_PATTERN_TYPES as readonly string[]).includes(pattern)) {
    return colors[pattern as TrendlinePatternType]
  }
  return colors.neutral
}

/** Geometry identity of a spec for the forming/confirmed collapse: same pattern
 * + same anchor points ⇒ same geometry (the detector emits a dashed `forming`
 * and a solid `confirmed` spec with identical `points`). */
function geometryKey(s: TrendlineSpec): string {
  return `${s.pattern ?? 'unknown'}|${s.points.map((p) => `${p.ts}@${p.price}`).join(';')}`
}

/**
 * Collapse the redundant forming(dashed)+confirmed(solid) twin (Plan 0067 /
 * ADR-0061): when a `solid` spec exists for a geometry, drop its `dashed` twin —
 * confirmed subsumes forming with no information loss. Forming-only and
 * confirmed-only geometries are both kept. Pure and order-preserving.
 */
export function dedupeTrendlines(specs: readonly TrendlineSpec[]): TrendlineSpec[] {
  const confirmedGeoms = new Set<string>()
  for (const s of specs) {
    if (s.style === 'solid') confirmedGeoms.add(geometryKey(s))
  }
  return specs.filter((s) => !(s.style === 'dashed' && confirmedGeoms.has(geometryKey(s))))
}

/** Human-readable names for the classical chart-pattern types the detector emits
 * (mirror of `CHART_PATTERNS`). Keyed by the wire `pattern` value; shared by the
 * hover tooltip and the grouped legend so they read identically. */
const PATTERN_DISPLAY_NAMES: Record<string, string> = {
  head_shoulders: 'Head & shoulders',
  inverse_head_shoulders: 'Inverse head & shoulders',
  double_top: 'Double top',
  double_bottom: 'Double bottom',
  ascending_triangle: 'Ascending triangle',
  descending_triangle: 'Descending triangle',
  symmetrical_triangle: 'Symmetrical triangle',
  rising_wedge: 'Rising wedge',
  falling_wedge: 'Falling wedge',
}

/** Display name for a pattern type; "Trendline" for an unknown/absent pattern. */
export function patternDisplayName(pattern: string | null | undefined): string {
  return (pattern != null && PATTERN_DISPLAY_NAMES[pattern]) || 'Trendline'
}

/** The legend/highlight grouping key of a spec — one row per (pattern type,
 * state) (Plan 0067 phase 3 / ADR-0061). `style` encodes state (solid=confirmed,
 * dashed=forming), so `${pattern}|${style}` groups a triangle's two solid bounds
 * into one row while keeping a forming twin separate. */
export function patternStateKey(s: TrendlineSpec): string {
  return `${s.pattern ?? 'unknown'}|${s.style}`
}

/** The `hidden`-set / legend-row layer id for a trendline group, namespaced
 * under `TRENDLINE_LAYER_ID` so it never collides with an overlay/marker id. */
export function trendlineGroupLayerId(key: string): string {
  return `${TRENDLINE_LAYER_ID}:${key}`
}

/** State word for a spec's `style`, for the legend row and the tooltip. */
export function trendlineStateLabel(style: TrendlineSpec['style']): string {
  return style === 'dashed' ? 'forming' : 'confirmed'
}

function toUtcSeconds(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp
}

/** One stroked line segment in media (pixel) coordinates. */
export interface TrendlineSegment {
  x1: number
  y1: number
  x2: number
  y2: number
  /** Colour resolved from the spec's pattern-type token. */
  color: string
  /** `style === "dashed"` → forming; solid → confirmed. */
  dashed: boolean
  /** Highlight emphasis (Plan 0067 phase 3): this segment belongs to the
   * hovered legend group — drawn thicker at full opacity. */
  emphasis?: boolean
  /** Highlight dim (Plan 0067 phase 3): a highlight is active and this segment
   * is NOT in the hovered group — drawn at reduced opacity. */
  dimmed?: boolean
  /** Breakout-arrow head (Plan 0083 ph3 / ADR-0078): set on a `projection`
   * segment — the vertical measured-move indicator. `'up'` points at a target
   * above the break (bullish), `'down'` below (bearish). The renderer draws a
   * filled arrowhead at the far (target) endpoint. */
  arrow?: 'up' | 'down'
}

/** Max forward horizon for apex extension, as a multiple of the pattern's pixel
 * width (Plan 0083 ph3 / ADR-0078): the two converging boundaries are extended
 * to their intersection only when the apex lies forward of the anchors and no
 * farther than this. Near-parallel boundaries put the apex far to the right, so
 * this bound is also the near-parallel fallback — beyond it, draw plain segments
 * rather than shooting a line off-screen. */
export const APEX_MAX_HORIZON_FACTOR = 3

/** A single-segment bounding line tagged for the apex-extension pass. */
interface ApexBoundary {
  seg: TrendlineSegment
  role: 'upper_trendline' | 'lower_trendline'
  /** `${pattern}|${style}` — the same key `patternStateKey` uses, so the two
   * boundaries of ONE formation (same pattern + state) pair up. */
  key: string
}

/**
 * Pure: intersection (in pixel space) of the two boundary segments' infinite
 * lines, or `null` when they are parallel, the apex is behind the anchors, or it
 * is beyond the forward horizon. The last two guards keep a near-parallel or
 * diverging pair from being extended off-screen (ADR-0078).
 */
export function computeApex(
  u: TrendlineSegment,
  l: TrendlineSegment,
): { x: number; y: number } | null {
  const ux = u.x2 - u.x1
  const uy = u.y2 - u.y1
  const lx = l.x2 - l.x1
  const ly = l.y2 - l.y1
  const denom = ux * ly - uy * lx
  if (denom === 0) return null // parallel — no apex
  const t = ((l.x1 - u.x1) * ly - (l.y1 - u.y1) * lx) / denom
  const ax = u.x1 + t * ux
  const ay = u.y1 + t * uy
  const rightMost = Math.max(u.x1, u.x2, l.x1, l.x2)
  const leftMost = Math.min(u.x1, u.x2, l.x1, l.x2)
  const width = rightMost - leftMost
  if (width <= 0) return null
  if (ax < rightMost) return null // apex not forward of the anchors
  if (ax > rightMost + APEX_MAX_HORIZON_FACTOR * width) return null // too far → near-parallel
  return { x: ax, y: ay }
}

/**
 * Extend each formation's upper+lower boundary to their shared apex, mutating the
 * segments' far (rightmost) endpoint in place (ADR-0078). Groups by
 * `${pattern}|${style}`; a group is extended only when it has EXACTLY one upper
 * and one lower boundary — multiple same-pattern formations are left as plain
 * segments (the deferred overlapping-formations case). The two boundaries are
 * separate specs, so this must run across specs, not per-spec.
 */
function extendBoundariesToApex(boundaries: readonly ApexBoundary[]): void {
  const groups = new Map<string, { upper: ApexBoundary[]; lower: ApexBoundary[] }>()
  for (const b of boundaries) {
    const g = groups.get(b.key) ?? { upper: [], lower: [] }
    if (b.role === 'upper_trendline') g.upper.push(b)
    else g.lower.push(b)
    groups.set(b.key, g)
  }
  for (const { upper, lower } of groups.values()) {
    if (upper.length !== 1 || lower.length !== 1) continue
    const apex = computeApex(upper[0].seg, lower[0].seg)
    if (apex === null) continue
    for (const { seg } of [upper[0], lower[0]]) {
      // The rightmost endpoint is the far one (points are time-ordered ascending).
      if (seg.x2 >= seg.x1) {
        seg.x2 = apex.x
        seg.y2 = apex.y
      } else {
        seg.x1 = apex.x
        seg.y1 = apex.y
      }
    }
  }
}

/**
 * Pure: map each spec's consecutive anchor pairs to pixel segments via a
 * time→x converter (the chart's `timeScale().timeToCoordinate`) and a price→y
 * converter (the candle series' `priceToCoordinate`). A segment whose either
 * endpoint maps off-screen (a converter returns `null`) is skipped — the
 * `computeSpanRects` precedent. Canvas-free, so the coordinate logic is
 * unit-tested without a real chart.
 */
export function computeTrendlineSegments(
  specs: ReadonlyArray<TrendlineSpec>,
  timeToX: (t: UTCTimestamp) => number | null,
  priceToY: (price: number) => number | null,
  colors: TrendlineColors,
): TrendlineSegment[] {
  const segments: TrendlineSegment[] = []
  const boundaries: ApexBoundary[] = []
  for (const spec of specs) {
    const color = trendlineColor(spec.pattern, colors)
    const dashed = spec.style === 'dashed'
    for (let i = 0; i + 1 < spec.points.length; i += 1) {
      const a = spec.points[i]
      const b = spec.points[i + 1]
      const x1 = timeToX(toUtcSeconds(a.ts))
      const y1 = priceToY(a.price)
      const x2 = timeToX(toUtcSeconds(b.ts))
      const y2 = priceToY(b.price)
      if (x1 === null || y1 === null || x2 === null || y2 === null) continue
      const seg: TrendlineSegment = { x1, y1, x2, y2, color, dashed }
      // The measured-move projection is a vertical segment; the arrowhead sits at
      // the far (target) end, up when the target is above the break else down.
      if (spec.role === 'projection') seg.arrow = y2 < y1 ? 'up' : 'down'
      segments.push(seg)
      if (
        spec.points.length === 2 &&
        (spec.role === 'upper_trendline' || spec.role === 'lower_trendline')
      ) {
        boundaries.push({ seg, role: spec.role, key: patternStateKey(spec) })
      }
    }
  }
  extendBoundariesToApex(boundaries)
  return segments
}

/** Default pixel tolerance for hovering a trendline (Plan 0067 phase 2 /
 * ADR-0061). Small enough that a hover near a dense cluster picks one line, big
 * enough that a 2px line is easy to catch; pinned in the hit-test unit test. */
export const TRENDLINE_HIT_TOLERANCE_PX = 5

/**
 * Pure: shortest distance (px) from point `(px,py)` to the segment
 * `(x1,y1)-(x2,y2)`. Projects the point onto the segment, clamping the parameter
 * to `[0,1]` so an off-the-end projection measures to the nearer endpoint; a
 * degenerate zero-length segment measures to its point.
 */
export function pointSegmentDistance(
  px: number,
  py: number,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
): number {
  const dx = x2 - x1
  const dy = y2 - y1
  const lenSq = dx * dx + dy * dy
  if (lenSq === 0) return Math.hypot(px - x1, py - y1)
  let t = ((px - x1) * dx + (py - y1) * dy) / lenSq
  t = Math.max(0, Math.min(1, t))
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
}

/**
 * Pure: index of the spec-group whose nearest drawn segment is closest to
 * `(px,py)` within `tolerance` px, or `null` when every group is farther.
 * `groups[i]` are the pixel segments of spec `i`. Ties resolve to the LAST group
 * (later specs draw over earlier, so the topmost line wins the hover).
 */
export function nearestTrendlineGroup(
  groups: readonly (readonly TrendlineSegment[])[],
  px: number,
  py: number,
  tolerance: number,
): number | null {
  let best: number | null = null
  let bestDist = tolerance
  for (let i = 0; i < groups.length; i += 1) {
    for (const seg of groups[i]) {
      const d = pointSegmentDistance(px, py, seg.x1, seg.y1, seg.x2, seg.y2)
      if (d <= bestDist) {
        bestDist = d
        best = i
      }
    }
  }
  return best
}

/**
 * Map an anchor time to a fractional logical index against ascending bar times:
 * integer `i` at `barTimes[i]`, linearly interpolated between neighbours, and
 * linearly EXTRAPOLATED beyond either end using the edge spacing (so a
 * projected neckline or a bound past the last bar still gets a logical). The
 * caller guarantees `barTimes.length >= 2`.
 */
export function timeToFractionalLogical(
  t: UTCTimestamp,
  barTimes: ReadonlyArray<UTCTimestamp>,
): number {
  const last = barTimes.length - 1
  if (t <= barTimes[0]) {
    const span = barTimes[1] - barTimes[0]
    return span === 0 ? 0 : (t - barTimes[0]) / span
  }
  if (t >= barTimes[last]) {
    const span = barTimes[last] - barTimes[last - 1]
    return span === 0 ? last : last + (t - barTimes[last]) / span
  }
  // Binary search for the bracket `barTimes[lo] <= t < barTimes[lo + 1]`.
  let lo = 0
  let hi = last
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (barTimes[mid] <= t) lo = mid
    else hi = mid
  }
  const span = barTimes[hi] - barTimes[lo]
  return span === 0 ? lo : lo + (t - barTimes[lo]) / span
}

/**
 * Resolve an anchor time to an x pixel coordinate. Fast path: the chart's
 * grid-snapped `timeToCoordinate`, which is non-null ONLY for a time that lands
 * exactly on a loaded bar. Off-grid / beyond-range times (a projected neckline,
 * a forming bound reaching the right edge, an anchor outside the loaded window)
 * make `timeToCoordinate` return `null`; we then interpolate/extrapolate x from
 * the loaded bar grid through the LOGICAL scale — `logicalToCoordinate`
 * extrapolates for out-of-range logicals, mirroring how `priceToCoordinate`
 * extrapolates on the price axis, so a sloped line still strokes (clipped
 * naturally by the canvas). Returns `null` only when the chart genuinely can't
 * place the time: an empty/degenerate grid (< 2 bars) — the sole surviving
 * skip case, NOT a blanket widening.
 */
export function resolveTimeX(
  t: UTCTimestamp,
  timeToCoordinate: (t: UTCTimestamp) => number | null,
  barTimes: ReadonlyArray<UTCTimestamp>,
  logicalToCoordinate: (logical: number) => number | null,
): number | null {
  const direct = timeToCoordinate(t)
  if (direct !== null) return direct
  if (barTimes.length < 2) return null
  const frac = timeToFractionalLogical(t, barTimes)
  const lo = Math.floor(frac)
  const xLo = logicalToCoordinate(lo)
  const xHi = logicalToCoordinate(lo + 1)
  if (xLo === null || xHi === null) return null
  return xLo + (frac - lo) * (xHi - xLo)
}

/** Ascending numeric (UTCTimestamp) times of the loaded candle bars. The candle
 * series is always keyed by `UTCTimestamp` (see `toLightweightBar`), so
 * non-numeric/whitespace items are defensively skipped. */
function barTimesFromSeries(data: ReadonlyArray<{ time: unknown }>): UTCTimestamp[] {
  const times: UTCTimestamp[] = []
  for (const d of data) {
    if (typeof d.time === 'number') times.push(d.time as UTCTimestamp)
  }
  return times
}

// The renderer's canvas target — the minimal slice of fancy-canvas'
// `CanvasRenderingTarget2D` we use (same local-typing rationale as spans.ts).
interface MediaCoordinateScope {
  context: CanvasRenderingContext2D
  mediaSize: { width: number; height: number }
}
interface TrendlineDrawTarget {
  useMediaCoordinateSpace(callback: (scope: MediaCoordinateScope) => void): void
}

class TrendlinePaneRenderer implements ISeriesPrimitivePaneRenderer {
  constructor(private readonly segments: TrendlineSegment[]) {}

  draw(target: TrendlineDrawTarget): void {
    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context
      for (const seg of this.segments) {
        ctx.save()
        ctx.strokeStyle = seg.color
        // Highlight (Plan 0067 phase 3): the hovered group's lines draw thicker
        // at full opacity, the rest dim to a wash. `save`/`restore` isolate the
        // alpha/width per segment.
        ctx.lineWidth = seg.emphasis ? 3 : 2
        ctx.globalAlpha = seg.dimmed ? 0.2 : 1
        // A projection (breakout arrow) draws DOTTED so it reads as a projected
        // target, visually distinct from the solid/dashed real boundaries — a
        // dashed forming boundary keeps its [6,4] dash (Plan 0083 ph3 / ADR-0078).
        ctx.setLineDash(seg.arrow ? [2, 3] : seg.dashed ? [6, 4] : [])
        ctx.beginPath()
        ctx.moveTo(seg.x1, seg.y1)
        ctx.lineTo(seg.x2, seg.y2)
        ctx.stroke()
        // Breakout arrowhead (Plan 0083 ph3 / ADR-0078): a filled triangle at the
        // projection's far (target) endpoint, pointing up (bullish) or down.
        if (seg.arrow) {
          const size = 7
          const dir = seg.arrow === 'up' ? -1 : 1
          ctx.setLineDash([])
          ctx.fillStyle = seg.color
          ctx.beginPath()
          ctx.moveTo(seg.x2, seg.y2)
          ctx.lineTo(seg.x2 - size * 0.6, seg.y2 - dir * size)
          ctx.lineTo(seg.x2 + size * 0.6, seg.y2 - dir * size)
          ctx.closePath()
          ctx.fill()
        }
        ctx.restore()
      }
    })
  }
}

class TrendlinePaneView implements ISeriesPrimitivePaneView {
  constructor(private readonly primitive: TrendlinePrimitive) {}

  zOrder(): SeriesPrimitivePaneViewZOrder {
    // Above the candles — a 2px neckline behind the bodies would be illegible
    // (unlike the span BAND, which deliberately paints behind them).
    return 'top'
  }

  renderer(): ISeriesPrimitivePaneRenderer {
    return new TrendlinePaneRenderer(this.primitive.currentSegments())
  }
}

/**
 * The series primitive the chart attaches once and feeds specs/colours/
 * visibility into. `paneViews()` returns the line view only while visible and
 * non-empty, so toggling the legend row or clearing the trendlines removes the
 * lines; the chart recomputes the segments on every pan/zoom because it
 * re-reads `paneViews`.
 */
export class TrendlinePrimitive implements ISeriesPrimitive<Time> {
  private specs: ReadonlyArray<TrendlineSpec> = []
  private colors: TrendlineColors
  private visible = true
  // The hovered legend group's `patternStateKey`, or null when nothing is
  // highlighted (Plan 0067 phase 3): matching lines draw emphasised, the rest dim.
  private highlightKey: string | null = null
  private chart: SeriesAttachedParameter<Time>['chart'] | null = null
  private series: SeriesAttachedParameter<Time>['series'] | null = null
  private requestUpdate: (() => void) | null = null
  private readonly paneView = new TrendlinePaneView(this)

  constructor(colors: TrendlineColors) {
    this.colors = colors
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.chart = param.chart
    this.series = param.series
    this.requestUpdate = param.requestUpdate
  }

  detached(): void {
    this.chart = null
    this.series = null
    this.requestUpdate = null
  }

  paneViews(): readonly ISeriesPrimitivePaneView[] {
    return this.visible && this.specs.length > 0 ? [this.paneView] : []
  }

  setTrendlines(specs: ReadonlyArray<TrendlineSpec>): void {
    this.specs = specs
    this.requestUpdate?.()
  }

  setColors(colors: TrendlineColors): void {
    this.colors = colors
    this.requestUpdate?.()
  }

  setVisible(visible: boolean): void {
    this.visible = visible
    this.requestUpdate?.()
  }

  /** Highlight the legend group with this `patternStateKey` (its lines emphasise,
   * the rest dim), or clear with `null` (Plan 0067 phase 3). */
  setHighlightedGroup(key: string | null): void {
    if (this.highlightKey === key) return
    this.highlightKey = key
    this.requestUpdate?.()
  }

  /** The currently highlighted group key, or null. Exposed for tests/assertions. */
  highlightedGroup(): string | null {
    return this.highlightKey
  }

  /** Current pixel segments (media coords) — read by the pane renderer and
   * asserted directly in tests via stubbed time/price scales. Empty until
   * attached. Off-grid anchor times (which grid-snapped `timeToCoordinate`
   * returns `null` for) are resolved through the bar-grid logical fallback in
   * `resolveTimeX`, so projected/extended lines still draw; the price axis
   * extrapolates natively via `priceToCoordinate`. */
  currentSegments(): TrendlineSegment[] {
    const groups = this.currentSegmentsBySpec()
    if (this.highlightKey === null) return groups.flat()
    // A highlight is active: emphasise the hovered group's segments, dim the rest
    // (Plan 0067 phase 3). Per-spec grouping lets us tag by `patternStateKey`.
    const out: TrendlineSegment[] = []
    this.specs.forEach((spec, i) => {
      const match = patternStateKey(spec) === this.highlightKey
      for (const seg of groups[i]) {
        out.push(match ? { ...seg, emphasis: true } : { ...seg, dimmed: true })
      }
    })
    return out
  }

  /** Pixel segments grouped per spec (parallel to `this.specs`) so a hit test
   * can map a hovered pixel back to the owning spec. `currentSegments()` is just
   * the flattened form. Empty until attached (chart/series present). */
  private currentSegmentsBySpec(): TrendlineSegment[][] {
    const timeScale = this.chart?.timeScale()
    const series = this.series
    if (!timeScale || !series) return []
    const barTimes = barTimesFromSeries(series.data())
    const timeToX = (t: UTCTimestamp): number | null =>
      resolveTimeX(
        t,
        (tt) => timeScale.timeToCoordinate(tt),
        barTimes,
        (logical) => timeScale.logicalToCoordinate(logical as Logical),
      )
    const priceToY = (p: number): number | null => series.priceToCoordinate(p)
    const perSpec = this.specs.map((spec) =>
      computeTrendlineSegments([spec], timeToX, priceToY, this.colors),
    )
    // Apex extension is cross-spec: a formation's upper and lower boundaries are
    // separate specs, so the per-spec calls above cannot pair them. Extend across
    // the groups here — this mutates the segments in place (Plan 0083 ph3).
    const boundaries: ApexBoundary[] = []
    this.specs.forEach((spec, i) => {
      const segs = perSpec[i]
      if (
        segs.length === 1 &&
        (spec.role === 'upper_trendline' || spec.role === 'lower_trendline')
      ) {
        boundaries.push({ seg: segs[0], role: spec.role, key: patternStateKey(spec) })
      }
    })
    extendBoundariesToApex(boundaries)
    return perSpec
  }

  /** The trendline spec drawn nearest the pixel `(x,y)` within the hover
   * tolerance, or `null` (Plan 0067 phase 2). Feeds the chart's hover tooltip
   * with the hovered line's pattern + state. Returns `null` while hidden. */
  hitTestTrendline(x: number, y: number): TrendlineSpec | null {
    if (!this.visible) return null
    const idx = nearestTrendlineGroup(
      this.currentSegmentsBySpec(),
      x,
      y,
      TRENDLINE_HIT_TOLERANCE_PX,
    )
    return idx === null ? null : this.specs[idx]
  }

  /** lightweight-charts' primitive hover hook: reports the hovered line so the
   * library sets a pointer cursor and populates `MouseEventParams.hoveredObjectId`
   * (Plan 0067 phase 2 / ADR-0061). The tooltip reads the spec via
   * `hitTestTrendline`; this only drives the cursor affordance. */
  hitTest(x: number, y: number): PrimitiveHoveredItem | null {
    if (!this.visible) return null
    const idx = nearestTrendlineGroup(
      this.currentSegmentsBySpec(),
      x,
      y,
      TRENDLINE_HIT_TOLERANCE_PX,
    )
    if (idx === null) return null
    return { externalId: `${TRENDLINE_LAYER_ID}:${idx}`, zOrder: 'top', cursorStyle: 'pointer' }
  }
}
