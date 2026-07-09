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
      segments.push({ x1, y1, x2, y2, color, dashed })
    }
  }
  return segments
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
        ctx.lineWidth = 2
        ctx.setLineDash(seg.dashed ? [6, 4] : [])
        ctx.beginPath()
        ctx.moveTo(seg.x1, seg.y1)
        ctx.lineTo(seg.x2, seg.y2)
        ctx.stroke()
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

  /** Current pixel segments (media coords) — read by the pane renderer and
   * asserted directly in tests via stubbed time/price scales. Empty until
   * attached. Off-grid anchor times (which grid-snapped `timeToCoordinate`
   * returns `null` for) are resolved through the bar-grid logical fallback in
   * `resolveTimeX`, so projected/extended lines still draw; the price axis
   * extrapolates natively via `priceToCoordinate`. */
  currentSegments(): TrendlineSegment[] {
    const timeScale = this.chart?.timeScale()
    const series = this.series
    if (!timeScale || !series) return []
    const barTimes = barTimesFromSeries(series.data())
    return computeTrendlineSegments(
      this.specs,
      (t) =>
        resolveTimeX(
          t,
          (tt) => timeScale.timeToCoordinate(tt),
          barTimes,
          (logical) => timeScale.logicalToCoordinate(logical as Logical),
        ),
      (p) => series.priceToCoordinate(p),
      this.colors,
    )
  }
}
