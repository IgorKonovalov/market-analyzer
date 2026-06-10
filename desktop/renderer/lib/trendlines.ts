/**
 * Chart trendlines → a lightweight-charts series primitive that strokes sloped
 * polylines anchored at `(time, price)` points (Plan 0052 phase 4, ADR-0049): a
 * head-and-shoulders neckline, the bounding lines of a triangle/wedge. Modelled
 * on `spans.ts` (ADR-0045): the primitive rides the chart's own coordinate
 * system and tracks pan/zoom for free.
 *
 * Unlike the span band (time-only), a trendline needs the PRICE axis too, so the
 * pixel math maps each anchor through both `timeScale().timeToCoordinate` and
 * the candle series' `priceToCoordinate` — the primitive therefore depends on
 * the candle series being attached and non-empty, and `currentSegments()`
 * returns `[]` until `attached()` has run (the `currentRects`-empty-until-
 * attached pattern); on an empty series `priceToCoordinate` yields `null` and
 * every segment is skipped.
 *
 * `style` is the forming-vs-confirmed cue: `dashed` = forming, `solid` =
 * confirmed. Colours resolve from the theme tokens by role (never hardcoded in
 * the draw path): a lower trendline reads support-like (bullish token), an
 * upper trendline resistance-like (bearish token), a neckline uses the accent
 * token, and a roleless line the neutral token.
 *
 * The pixel math lives in the pure, canvas-free `computeTrendlineSegments` so
 * it is unit-testable; the renderer's `draw` only strokes the segments.
 */
import type {
  ISeriesPrimitive,
  ISeriesPrimitivePaneRenderer,
  ISeriesPrimitivePaneView,
  SeriesAttachedParameter,
  SeriesPrimitivePaneViewZOrder,
  Time,
  UTCTimestamp,
} from 'lightweight-charts'

import type { TrendlineRole, TrendlineSpec } from '../types/events'

/** Single legend row id/label controlling ALL trendlines (Plan 0052 phase 4),
 * mirroring the span layer's `SPAN_LAYER_ID` pattern. */
export const TRENDLINE_LAYER_ID = 'trendlines'
export const TRENDLINE_LAYER_LABEL = 'Trendlines'

/** Theme-resolved colours the trendline roles map onto. Same shape discipline
 * as `MarkerColors`: resolved from the DOM tokens by the caller, passed in. */
export interface TrendlineColors {
  /** `--marker-bullish` — lower trendlines (support-like). */
  bullish: string
  /** `--marker-bearish` — upper trendlines (resistance-like). */
  bearish: string
  /** `--marker-neutral` — roleless lines. */
  neutral: string
  /** `--marker-clicked` (the accent) — necklines. */
  accent: string
}

// Light-theme fallbacks for when a token is unset — e.g. in jsdom unit tests
// where styles.css isn't loaded. At runtime the tokens in styles.css win.
const TRENDLINE_COLOR_FALLBACK: TrendlineColors = {
  bullish: '#16a34a',
  bearish: '#dc2626',
  neutral: '#64748b',
  accent: '#2563eb',
}

/** Resolve the trendline palette off the themed DOM (lightweight-charts/canvas
 * can't resolve `var(--x)` strings), falling back to the light defaults. */
export function readTrendlineColors(el: HTMLElement): TrendlineColors {
  const c = getComputedStyle(el)
  const v = (name: string, fallback: string): string => c.getPropertyValue(name).trim() || fallback
  return {
    bullish: v('--marker-bullish', TRENDLINE_COLOR_FALLBACK.bullish),
    bearish: v('--marker-bearish', TRENDLINE_COLOR_FALLBACK.bearish),
    neutral: v('--marker-neutral', TRENDLINE_COLOR_FALLBACK.neutral),
    accent: v('--marker-clicked', TRENDLINE_COLOR_FALLBACK.accent),
  }
}

/** Role → theme token. Exported for the legend swatch (the row's colour must
 * equal the colour the lines are drawn with). */
export function trendlineColor(
  role: TrendlineRole | null | undefined,
  colors: TrendlineColors,
): string {
  if (role === 'neckline') return colors.accent
  if (role === 'upper_trendline') return colors.bearish
  if (role === 'lower_trendline') return colors.bullish
  return colors.neutral
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
  /** Colour resolved from the spec's role token. */
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
    const color = trendlineColor(spec.role, colors)
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
   * attached (and empty-series anchors are skipped: `priceToCoordinate`
   * returns `null` until the candle series has data). */
  currentSegments(): TrendlineSegment[] {
    const timeScale = this.chart?.timeScale()
    const series = this.series
    if (!timeScale || !series) return []
    return computeTrendlineSegments(
      this.specs,
      (t) => timeScale.timeToCoordinate(t),
      (p) => series.priceToCoordinate(p),
      this.colors,
    )
  }
}
