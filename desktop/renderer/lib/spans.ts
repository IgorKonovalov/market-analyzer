/**
 * Multi-bar pattern spans → a lightweight-charts series primitive that draws a
 * translucent vertical band over the bars a multi-bar pattern occupies (Plan 0049
 * phase 7, ADR-0045). Single-bar patterns carry no span and render only their
 * glyph (see `markers.ts`); a marker is a span iff it has both `span_*` endpoints.
 *
 * lightweight-charts 4.2.3 exposes series primitives (`ISeriesPrimitive` +
 * `series.attachPrimitive`), so the band rides the chart's own coordinate system
 * and tracks pan/zoom for free — no manual visible-range subscription. The pane
 * view's `zOrder()` is `'bottom'`, so the band paints behind the candles.
 *
 * The pixel math lives in the pure, canvas-free `computeSpanRects` so it is
 * unit-testable; the renderer's `draw` only blits the rectangles it returns.
 */
import type {
  ISeriesPrimitive,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  SeriesAttachedParameter,
  PrimitivePaneViewZOrder,
  Time,
  UTCTimestamp,
} from 'lightweight-charts'

import type { MarkerKind } from '../types/events'
import type { ChartMarker, MarkerColors } from './markers'

/** A resolved multi-bar pattern span: the first and last bar timestamps it
 * occupies, plus its direction (for colour) and pattern name (identity). */
export interface PatternSpan {
  startTs: string
  endTs: string
  kind: MarkerKind
  pattern: string | null
}

/** Single legend row id/label controlling ALL span boxes (Plan 0049 phase 7). */
export const SPAN_LAYER_ID = 'spans'
export const SPAN_LAYER_LABEL = 'Pattern spans'

/** Extract the spans from a marker list: only markers carrying BOTH span
 * endpoints are spans; single-bar markers (no `span_*`) are excluded. */
export function markersToSpans(markers: ChartMarker[]): PatternSpan[] {
  const spans: PatternSpan[] = []
  for (const m of markers) {
    if (m.span_start_ts != null && m.span_end_ts != null) {
      spans.push({
        startTs: m.span_start_ts,
        endTs: m.span_end_ts,
        kind: m.kind,
        pattern: m.pattern ?? null,
      })
    }
  }
  return spans
}

/**
 * Bounding highlight range for the markers under the crosshair (Plan 0071
 * follow-up): the union of their bars — from the earliest `span_start_ts` (or
 * `event_ts` for a single-bar marker) to the latest `span_end_ts` (or
 * `event_ts`). A single-bar marker yields a zero-width range the primitive
 * widens to a box around its bar. `null` for no markers. The direction/pattern
 * come from the first marker (usually the only one on a bar). */
export function markerHighlightSpan(markers: readonly ChartMarker[]): PatternSpan | null {
  if (markers.length === 0) return null
  let startTs = markers[0].span_start_ts ?? markers[0].event_ts
  let endTs = markers[0].span_end_ts ?? markers[0].event_ts
  for (const m of markers) {
    const s = m.span_start_ts ?? m.event_ts
    const e = m.span_end_ts ?? m.event_ts
    if (s < startTs) startTs = s
    if (e > endTs) endTs = e
  }
  return { startTs, endTs, kind: markers[0].kind, pattern: markers[0].pattern ?? null }
}

function toUtcSeconds(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp
}

function spanBaseColor(kind: MarkerKind, colors: MarkerColors): string {
  if (kind === 'bullish_marker') return colors.bullish
  if (kind === 'bearish_marker') return colors.bearish
  return colors.neutral
}

/** Append a ~22% alpha byte to a `#rrggbb` so the band reads as a translucent
 * highlight behind the candles. A non-hex colour passes through unchanged. */
function translucent(color: string): string {
  const m = /^#([0-9a-f]{6})$/i.exec(color.trim())
  return m ? `#${m[1]}38` : color
}

export interface SpanRect {
  /** Left pixel (media coordinates), <= x2. */
  x1: number
  /** Right pixel. */
  x2: number
  /** Translucent fill colour resolved from the span's direction token. */
  color: string
}

/**
 * Pure: map spans to pixel rectangles via a time→x converter (the chart's
 * `timeScale().timeToCoordinate`). A span whose either endpoint maps off-screen
 * (the converter returns `null`) is skipped. Canvas-free, so the coordinate logic
 * is unit-tested without a real chart.
 */
export function computeSpanRects(
  spans: PatternSpan[],
  timeToX: (t: UTCTimestamp) => number | null,
  colors: MarkerColors,
): SpanRect[] {
  const rects: SpanRect[] = []
  for (const span of spans) {
    const a = timeToX(toUtcSeconds(span.startTs))
    const b = timeToX(toUtcSeconds(span.endTs))
    if (a === null || b === null) continue
    rects.push({
      x1: Math.min(a, b),
      x2: Math.max(a, b),
      color: translucent(spanBaseColor(span.kind, colors)),
    })
  }
  return rects
}

// The renderer's canvas target — the minimal slice of fancy-canvas'
// `CanvasRenderingTarget2D` we use. Typed locally so this file doesn't import the
// (pnpm-nested) `fancy-canvas` package directly; method-param bivariance makes the
// renderer structurally assignable to `IPrimitivePaneRenderer`.
interface MediaCoordinateScope {
  context: CanvasRenderingContext2D
  mediaSize: { width: number; height: number }
}
interface SpanDrawTarget {
  useMediaCoordinateSpace(callback: (scope: MediaCoordinateScope) => void): void
}

class SpanPaneRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly rects: SpanRect[]) {}

  draw(target: SpanDrawTarget): void {
    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context
      const height = scope.mediaSize.height
      for (const rect of this.rects) {
        ctx.fillStyle = rect.color
        ctx.fillRect(rect.x1, 0, Math.max(1, rect.x2 - rect.x1), height)
      }
    })
  }
}

class SpanPaneView implements IPrimitivePaneView {
  constructor(private readonly primitive: PatternSpanPrimitive) {}

  zOrder(): PrimitivePaneViewZOrder {
    return 'bottom'
  }

  renderer(): IPrimitivePaneRenderer {
    return new SpanPaneRenderer(this.primitive.currentRects())
  }
}

/** Draws the hovered-pattern highlight (Plan 0071 follow-up): a full-height
 * STROKED border box around the pattern's bar(s) in its opaque direction colour,
 * so hovering a marker arrow outlines exactly which candles the pattern occupies. */
class SpanHighlightPaneRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly rect: SpanRect | null) {}

  draw(target: SpanDrawTarget): void {
    const rect = this.rect
    if (rect === null) return
    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context
      ctx.save()
      ctx.strokeStyle = rect.color
      ctx.lineWidth = 2
      ctx.strokeRect(rect.x1, 1, Math.max(1, rect.x2 - rect.x1), scope.mediaSize.height - 2)
      ctx.restore()
    })
  }
}

class SpanHighlightPaneView implements IPrimitivePaneView {
  constructor(private readonly primitive: PatternSpanPrimitive) {}

  zOrder(): PrimitivePaneViewZOrder {
    // Above the candles — the outline must be visible, unlike the translucent
    // band, which deliberately sits behind them.
    return 'top'
  }

  renderer(): IPrimitivePaneRenderer {
    return new SpanHighlightPaneRenderer(this.primitive.currentHighlightRect())
  }
}

/**
 * The series primitive the chart attaches once and feeds spans/colours/visibility
 * into. `paneViews()` returns the band view only while visible and non-empty, so
 * toggling the legend row or clearing the spans removes the boxes; the chart
 * recomputes the band on every pan/zoom because it re-reads `paneViews`.
 */
export class PatternSpanPrimitive implements ISeriesPrimitive<Time> {
  private spans: PatternSpan[] = []
  private colors: MarkerColors
  private visible = true
  // The hovered-pattern highlight range (Plan 0071 follow-up), or null when no
  // marker is under the crosshair. Drawn as a bordered box, independent of the
  // translucent band, so a single-bar pattern (no span) still outlines.
  private highlight: PatternSpan | null = null
  private chart: SeriesAttachedParameter<Time>['chart'] | null = null
  private requestUpdate: (() => void) | null = null
  private readonly paneView = new SpanPaneView(this)
  private readonly highlightView = new SpanHighlightPaneView(this)

  constructor(colors: MarkerColors) {
    this.colors = colors
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.chart = param.chart
    this.requestUpdate = param.requestUpdate
  }

  detached(): void {
    this.chart = null
    this.requestUpdate = null
  }

  paneViews(): readonly IPrimitivePaneView[] {
    const views: IPrimitivePaneView[] = []
    if (this.visible && this.spans.length > 0) views.push(this.paneView)
    if (this.highlight !== null) views.push(this.highlightView)
    return views
  }

  setSpans(spans: PatternSpan[]): void {
    this.spans = spans
    this.requestUpdate?.()
  }

  setColors(colors: MarkerColors): void {
    this.colors = colors
    this.requestUpdate?.()
  }

  setVisible(visible: boolean): void {
    this.visible = visible
    this.requestUpdate?.()
  }

  /** Highlight the pattern under the crosshair (Plan 0071 follow-up): outlines
   * its bar(s) with a bordered box, or clears with `null`. */
  setHighlight(highlight: PatternSpan | null): void {
    this.highlight = highlight
    this.requestUpdate?.()
  }

  /** Current pixel rectangles (media coords) — read by the pane renderer and
   * asserted directly in tests via a stubbed time scale. Empty until attached. */
  currentRects(): SpanRect[] {
    const timeScale = this.chart?.timeScale()
    if (!timeScale) return []
    return computeSpanRects(this.spans, (t) => timeScale.timeToCoordinate(t), this.colors)
  }

  /** The hovered-pattern highlight box in pixel coords (media), padded ~half a
   * bar each side so a single-bar pattern reads as a box around its candle and a
   * multi-bar span sits just outside its bars. `null` until attached, when no
   * highlight is set, or when either endpoint maps off-screen. */
  currentHighlightRect(): SpanRect | null {
    const timeScale = this.chart?.timeScale()
    if (!timeScale || this.highlight === null) return null
    const a = timeScale.timeToCoordinate(toUtcSeconds(this.highlight.startTs))
    const b = timeScale.timeToCoordinate(toUtcSeconds(this.highlight.endTs))
    if (a === null || b === null) return null
    const pad = timeScale.options().barSpacing / 2 + 2
    return {
      x1: Math.min(a, b) - pad,
      x2: Math.max(a, b) + pad,
      color: spanBaseColor(this.highlight.kind, this.colors),
    }
  }
}
