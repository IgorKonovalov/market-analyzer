/**
 * Divergence segments → a lightweight-charts series primitive that strokes the
 * two-point connecting lines of a price↔oscillator divergence (Plan 0091 phase 9,
 * ADR-0090). A divergence draws as TWO segments across two panes — one across the
 * price pivots on the price pane, one across the oscillator pivots on that
 * oscillator's own pane — so the caller attaches one `DivergencePrimitive` per
 * pane series and feeds each the segment(s) whose y-coordinates live on that pane.
 *
 * Modelled on `lib/trendlines.ts`: the primitive rides the chart's coordinate
 * system (tracking pan/zoom for free), maps anchor times through the shared
 * off-grid `resolveTimeX` fallback and anchor prices through the attached series'
 * `priceToCoordinate`. Colour is keyed by divergence CLASS × DIRECTION (regular vs
 * hidden, bullish vs bearish) — four distinct hues — not by chart-pattern type, so
 * this is a separate primitive rather than a reuse of `TrendlinePrimitive`.
 *
 * The pixel math lives in the pure, canvas-free `computeDivergenceSegments` so it
 * is unit-testable without a real chart. Conditions-only geometry — a divergence is
 * never a buy/sell call.
 */
import type {
  ISeriesPrimitive,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  Logical,
  PrimitiveHoveredItem,
  SeriesAttachedParameter,
  PrimitivePaneViewZOrder,
  Time,
  UTCTimestamp,
} from 'lightweight-charts'

import { resolveTimeX } from './trendlines'
import type { Divergence, DivergenceKind } from '../types/events'

/** The legend-row id/label governing ALL divergence segments, mirroring the
 * trendline layer's single-row pattern. */
export const DIVERGENCE_LAYER_ID = 'divergences'
export const DIVERGENCE_LAYER_LABEL = 'Divergences'

/** Theme-resolved categorical palette: one hue per divergence class×direction.
 * Bearish reds/oranges, bullish greens/teals; regular = saturated, hidden =
 * muted, so the four read as two families. Resolved from DOM tokens by the caller
 * (canvas can't resolve `var(--x)`), falling back to these light defaults. */
export type DivergenceColors = Record<DivergenceKind, string>

const DIVERGENCE_COLOR_FALLBACK: DivergenceColors = {
  regular_bearish: '#e03131',
  regular_bullish: '#2f9e44',
  hidden_bearish: '#e8590c',
  hidden_bullish: '#0c8599',
}

/** The `--divergence-<kind>` CSS-variable name (underscores → hyphens). */
function divergenceToken(kind: DivergenceKind): string {
  return `--divergence-${kind.replaceAll('_', '-')}`
}

/** The light-theme fallback palette as a fresh object — used to construct a
 * primitive before a themed container is available (the oscillator panes attach
 * their primitive at pane-creation; `useDivergences` recolours off the DOM). */
export function fallbackDivergenceColors(): DivergenceColors {
  return { ...DIVERGENCE_COLOR_FALLBACK }
}

/** Resolve the categorical divergence palette off the themed DOM, falling back to
 * the light defaults (e.g. in jsdom unit tests where styles.css isn't loaded). */
export function readDivergenceColors(el: HTMLElement): DivergenceColors {
  const c = getComputedStyle(el)
  const colors = {} as DivergenceColors
  for (const kind of Object.keys(DIVERGENCE_COLOR_FALLBACK) as DivergenceKind[]) {
    colors[kind] =
      c.getPropertyValue(divergenceToken(kind)).trim() || DIVERGENCE_COLOR_FALLBACK[kind]
  }
  return colors
}

/** Divergence kind → theme colour. Exported for the legend swatch parity. */
export function divergenceColor(kind: DivergenceKind, colors: DivergenceColors): string {
  return colors[kind]
}

/** Human-readable label for a divergence kind, for the segment label + tooltip. */
const DIVERGENCE_DISPLAY_NAMES: Record<DivergenceKind, string> = {
  regular_bearish: 'Regular bearish divergence',
  regular_bullish: 'Regular bullish divergence',
  hidden_bearish: 'Hidden bearish divergence',
  hidden_bullish: 'Hidden bullish divergence',
}

export function divergenceLabel(kind: DivergenceKind): string {
  return DIVERGENCE_DISPLAY_NAMES[kind]
}

/** The glossary key for a divergence kind (the `divergence` category, Plan 0091
 * phase 9). One entry per class×direction. */
export function divergenceGlossaryKey(kind: DivergenceKind): string {
  return `divergence_${kind}`
}

/** Which side of a divergence a primitive draws: the price pivots (on pane 0) or
 * the oscillator pivots (on that oscillator's own pane). */
export type DivergenceSide = 'price' | 'oscillator'

/** Map a divergence's `oscillator` to the `OverlayKind` of the pane its oscillator
 * segment draws on. `macd_hist` draws on the `macd` histogram pane; `obv` uses the
 * always-on OBV base pane (handled outside `useOscillatorPanes`). */
export function divergenceOscillatorToPaneKind(
  oscillator: Divergence['oscillator'],
): 'rsi' | 'macd' | 'mfi' | 'obv' {
  return oscillator === 'macd_hist' ? 'macd' : oscillator
}

function toUtcSeconds(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp
}

/** One stroked divergence segment in media (pixel) coordinates. */
export interface DivergenceSegment {
  x1: number
  y1: number
  x2: number
  y2: number
  color: string
  kind: DivergenceKind
}

/**
 * Pure: map each divergence's chosen-side pivot pair to a pixel segment via a
 * time→x converter and a price→y converter. A segment whose either endpoint maps
 * off-screen (a converter returns `null`) or that lacks two anchors is skipped —
 * the `computeTrendlineSegments` precedent. Canvas-free, so unit-tested without a
 * real chart.
 */
export function computeDivergenceSegments(
  divergences: ReadonlyArray<Divergence>,
  side: DivergenceSide,
  timeToX: (t: UTCTimestamp) => number | null,
  priceToY: (price: number) => number | null,
  colors: DivergenceColors,
): DivergenceSegment[] {
  const segments: DivergenceSegment[] = []
  for (const d of divergences) {
    const pivots = side === 'price' ? d.price_pivots : d.oscillator_pivots
    if (pivots.length < 2) continue
    const [a, b] = [pivots[0], pivots[pivots.length - 1]]
    const x1 = timeToX(toUtcSeconds(a.ts))
    const y1 = priceToY(a.price)
    const x2 = timeToX(toUtcSeconds(b.ts))
    const y2 = priceToY(b.price)
    if (x1 === null || y1 === null || x2 === null || y2 === null) continue
    segments.push({ x1, y1, x2, y2, color: colors[d.kind], kind: d.kind })
  }
  return segments
}

/** Pure: shortest distance (px) from a point to a segment — the trendline
 * hit-test's projection-clamped formula, inlined to keep this module standalone. */
export function pointToSegmentDistance(
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

/** Hover tolerance (px) — matches the trendline primitive so the two feel alike. */
export const DIVERGENCE_HIT_TOLERANCE_PX = 5

const DIVERGENCE_LINE_WIDTH = 2

// The minimal slice of the canvas target we use (same local-typing rationale as
// spans.ts / trendlines.ts).
interface MediaCoordinateScope {
  context: CanvasRenderingContext2D
  mediaSize: { width: number; height: number }
}
interface DivergenceDrawTarget {
  useMediaCoordinateSpace(callback: (scope: MediaCoordinateScope) => void): void
}

class DivergencePaneRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly segments: DivergenceSegment[]) {}

  draw(target: DivergenceDrawTarget): void {
    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context
      for (const seg of this.segments) {
        ctx.save()
        ctx.strokeStyle = seg.color
        ctx.lineWidth = DIVERGENCE_LINE_WIDTH
        // Divergence lines are drawn dashed so they read distinctly from solid
        // pattern trendlines and the candle wicks beneath them.
        ctx.setLineDash([5, 3])
        ctx.beginPath()
        ctx.moveTo(seg.x1, seg.y1)
        ctx.lineTo(seg.x2, seg.y2)
        ctx.stroke()
        // A small filled dot at each anchor marks the paired pivots.
        ctx.setLineDash([])
        ctx.fillStyle = seg.color
        for (const [x, y] of [
          [seg.x1, seg.y1],
          [seg.x2, seg.y2],
        ]) {
          ctx.beginPath()
          ctx.arc(x, y, 3, 0, Math.PI * 2)
          ctx.fill()
        }
        ctx.restore()
      }
    })
  }
}

class DivergencePaneView implements IPrimitivePaneView {
  constructor(private readonly primitive: DivergencePrimitive) {}

  zOrder(): PrimitivePaneViewZOrder {
    return 'top'
  }

  renderer(): IPrimitivePaneRenderer {
    return new DivergencePaneRenderer(this.primitive.currentSegments())
  }
}

/**
 * The series primitive the chart attaches once per pane and feeds divergences +
 * colours + visibility into. `paneViews()` returns the view only while visible and
 * non-empty, so clearing the divergences or toggling the layer off removes the
 * lines; the chart recomputes the segments on every pan/zoom because it re-reads
 * `paneViews`. `side` fixes which pivot pair this instance draws (price pane vs the
 * oscillator pane it is attached to).
 */
export class DivergencePrimitive implements ISeriesPrimitive<Time> {
  private divergences: ReadonlyArray<Divergence> = []
  private colors: DivergenceColors
  private visible = true
  private chart: SeriesAttachedParameter<Time>['chart'] | null = null
  private series: SeriesAttachedParameter<Time>['series'] | null = null
  private requestUpdate: (() => void) | null = null
  private readonly paneView = new DivergencePaneView(this)

  constructor(
    private readonly side: DivergenceSide,
    colors: DivergenceColors,
  ) {
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

  paneViews(): readonly IPrimitivePaneView[] {
    return this.visible && this.divergences.length > 0 ? [this.paneView] : []
  }

  setDivergences(divergences: ReadonlyArray<Divergence>): void {
    this.divergences = divergences
    this.requestUpdate?.()
  }

  setColors(colors: DivergenceColors): void {
    this.colors = colors
    this.requestUpdate?.()
  }

  setVisible(visible: boolean): void {
    this.visible = visible
    this.requestUpdate?.()
  }

  private converters(): {
    timeToX: (t: UTCTimestamp) => number | null
    priceToY: (price: number) => number | null
  } | null {
    const timeScale = this.chart?.timeScale()
    const series = this.series
    if (!timeScale || !series) return null
    const barTimes: UTCTimestamp[] = []
    for (const d of series.data()) {
      if (typeof d.time === 'number') barTimes.push(d.time as UTCTimestamp)
    }
    const timeToX = (t: UTCTimestamp): number | null =>
      resolveTimeX(
        t,
        (tt) => timeScale.timeToCoordinate(tt),
        barTimes,
        (logical) => timeScale.logicalToCoordinate(logical as Logical),
      )
    const priceToY = (p: number): number | null => series.priceToCoordinate(p)
    return { timeToX, priceToY }
  }

  /** Current pixel segments (media coords) — read by the pane renderer and
   * asserted directly in tests via stubbed time/price scales. Empty until
   * attached. */
  currentSegments(): DivergenceSegment[] {
    const conv = this.converters()
    if (conv === null) return []
    return computeDivergenceSegments(
      this.divergences,
      this.side,
      conv.timeToX,
      conv.priceToY,
      this.colors,
    )
  }

  /** The divergence drawn nearest the pixel `(x,y)` within the hover tolerance, or
   * `null` — feeds the chart's hover tooltip with the hovered divergence's kind.
   * Returns `null` while hidden. */
  hitTestDivergence(x: number, y: number): Divergence | null {
    if (!this.visible) return null
    const segs = this.currentSegments()
    let best: number | null = null
    let bestDist = DIVERGENCE_HIT_TOLERANCE_PX
    for (let i = 0; i < segs.length; i += 1) {
      const s = segs[i]
      const d = pointToSegmentDistance(x, y, s.x1, s.y1, s.x2, s.y2)
      if (d <= bestDist) {
        bestDist = d
        best = i
      }
    }
    return best === null ? null : this.divergences[best]
  }

  hitTest(x: number, y: number): PrimitiveHoveredItem | null {
    if (!this.visible) return null
    if (this.hitTestDivergence(x, y) === null) return null
    return { externalId: DIVERGENCE_LAYER_ID, zOrder: 'top', cursorStyle: 'pointer' }
  }
}
