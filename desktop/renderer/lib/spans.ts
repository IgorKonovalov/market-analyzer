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
  ISeriesPrimitivePaneRenderer,
  ISeriesPrimitivePaneView,
  SeriesAttachedParameter,
  SeriesPrimitivePaneViewZOrder,
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
// renderer structurally assignable to `ISeriesPrimitivePaneRenderer`.
interface MediaCoordinateScope {
  context: CanvasRenderingContext2D
  mediaSize: { width: number; height: number }
}
interface SpanDrawTarget {
  useMediaCoordinateSpace(callback: (scope: MediaCoordinateScope) => void): void
}

class SpanPaneRenderer implements ISeriesPrimitivePaneRenderer {
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

class SpanPaneView implements ISeriesPrimitivePaneView {
  constructor(private readonly primitive: PatternSpanPrimitive) {}

  zOrder(): SeriesPrimitivePaneViewZOrder {
    return 'bottom'
  }

  renderer(): ISeriesPrimitivePaneRenderer {
    return new SpanPaneRenderer(this.primitive.currentRects())
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
  private chart: SeriesAttachedParameter<Time>['chart'] | null = null
  private requestUpdate: (() => void) | null = null
  private readonly paneView = new SpanPaneView(this)

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

  paneViews(): readonly ISeriesPrimitivePaneView[] {
    return this.visible && this.spans.length > 0 ? [this.paneView] : []
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

  /** Current pixel rectangles (media coords) — read by the pane renderer and
   * asserted directly in tests via a stubbed time scale. Empty until attached. */
  currentRects(): SpanRect[] {
    const timeScale = this.chart?.timeScale()
    if (!timeScale) return []
    return computeSpanRects(this.spans, (t) => timeScale.timeToCoordinate(t), this.colors)
  }
}
