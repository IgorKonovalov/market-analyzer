/**
 * User/agent freeform drawings → a lightweight-charts series primitive that
 * strokes `DrawingSpec` geometry anchored at `(time, price)` (Plan 0097, ADR-0091).
 *
 * Modelled on `trendlines.ts` (the pattern-trendline primitive) but for the
 * editable, user-authored drawing layer: it renders the six drawing kinds, marks
 * the SELECTED drawing with endpoint handles, and hit-tests drawings + handles so
 * the edit engine (`useDrawingTools`) can select and drag. It reuses the pure
 * ADR-0059 logical-coordinate helpers (`resolveTimeX` / `timeToFractionalLogical`)
 * and `pointSegmentDistance` from `trendlines.ts` — a projected/off-grid anchor
 * (a `ray` past the last bar) still maps rather than being dropped.
 *
 * Phase 2 (walking skeleton) implements `trendline` + `ray`; `hline` / `vline` /
 * `rect` / `fib` land in phase 3, which extends `computeDrawingGeometry`. The
 * pixel math lives in the pure, canvas-free `computeDrawingGeometry` /
 * `computeRayFarPoint` so it is unit-testable without a real chart.
 */
import type {
  ISeriesPrimitive,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  Logical,
  PrimitiveHoveredItem,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  Time,
  UTCTimestamp,
} from 'lightweight-charts'

import type { DrawingKind, DrawingSpec } from '../types/events'
import { pointSegmentDistance, resolveTimeX } from './trendlines'

/** Default stroke colour for a user drawing when its `style.color` is unset. A
 * mid blue that reads on both light and dark themes. */
export const DEFAULT_DRAWING_COLOR = '#2962ff'
/** Colour of an agent-placed drawing when unstyled (Plan 0097 phase 4) — a warm
 * amber, distinct from the user default so provenance reads at a glance. */
export const DEFAULT_AGENT_DRAWING_COLOR = '#f08c00'
/** Default stroke width (px) when `style.width` is unset. */
export const DEFAULT_DRAWING_WIDTH = 2

/** Half-side (px) of the square endpoint handle drawn on the selected drawing. */
export const HANDLE_HALF_PX = 4
/** Pixel tolerance for grabbing a drawing's line (select) — matches the
 * trendline hover tolerance so the two layers feel the same. */
export const DRAWING_HIT_TOLERANCE_PX = 6
/** Pixel tolerance for grabbing an endpoint handle (drag). Slightly larger than
 * the handle so it's easy to catch. */
export const HANDLE_HIT_TOLERANCE_PX = 8

/** Anchor-count per drawing kind — the geometry contract the sidecar `DrawingSpec`
 * enforces (1 for hline/vline, 2 for the rest), mirrored on the renderer so the
 * store drops a malformed record and the tool machine knows how many clicks a
 * placement takes. */
export const POINT_COUNT_BY_KIND: Record<DrawingKind, number> = {
  trendline: 2,
  ray: 2,
  hline: 1,
  vline: 1,
  rect: 2,
  fib: 2,
}

function toUtcSeconds(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp
}

/** A stroked line segment in media (pixel) coordinates. */
export interface DrawingSegment {
  x1: number
  y1: number
  x2: number
  y2: number
}

/** An endpoint handle position in media (pixel) coordinates. */
export interface DrawingHandle {
  x: number
  y: number
}

/** The pixel geometry of one drawing: the stroked line(s), the anchor handles a
 * drag grabs, and the resolved stroke style. `segments` is empty when an anchor
 * maps off-screen (a converter returns `null`). */
export interface DrawingGeometry {
  id: string
  kind: DrawingKind
  segments: DrawingSegment[]
  handles: DrawingHandle[]
  color: string
  width: number
}

/**
 * Pure: the far endpoint of a ray from `(x1,y1)` through `(x2,y2)`, extended in
 * that direction to the nearest chart edge (so the canvas clips it at the visible
 * boundary). A degenerate zero-length ray returns its second anchor.
 */
export function computeRayFarPoint(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  width: number,
  height: number,
): { x: number; y: number } {
  const dx = x2 - x1
  const dy = y2 - y1
  if (dx === 0 && dy === 0) return { x: x2, y: y2 }
  const ts: number[] = []
  if (dx > 0) ts.push((width - x1) / dx)
  else if (dx < 0) ts.push((0 - x1) / dx)
  if (dy > 0) ts.push((height - y1) / dy)
  else if (dy < 0) ts.push((0 - y1) / dy)
  const forward = ts.filter((t) => t > 0 && Number.isFinite(t))
  const t = forward.length > 0 ? Math.min(...forward) : 1
  return { x: x1 + t * dx, y: y1 + t * dy }
}

function styleColor(spec: DrawingSpec, fallback: string): string {
  return spec.style?.color ?? fallback
}

function styleWidth(spec: DrawingSpec): number {
  const w = spec.style?.width
  return typeof w === 'number' && w > 0 ? w : DEFAULT_DRAWING_WIDTH
}

/**
 * Pure: map one `DrawingSpec` to its pixel geometry via a time→x converter (with
 * the off-grid `resolveTimeX` fallback) and a price→y converter. Phase 2 handles
 * `trendline` (segment between the two anchors) and `ray` (from anchor 0 through
 * anchor 1, extended to the chart edge). Other kinds resolve their handles but
 * draw no segment yet (phase 3). Returns `null` when a defining anchor maps
 * off-screen. `mediaWidth`/`mediaHeight` bound the ray extension.
 */
export function computeDrawingGeometry(
  spec: DrawingSpec,
  timeToX: (t: UTCTimestamp) => number | null,
  priceToY: (price: number) => number | null,
  mediaWidth: number,
  mediaHeight: number,
  defaultColor: string = DEFAULT_DRAWING_COLOR,
): DrawingGeometry | null {
  const color = styleColor(spec, defaultColor)
  const width = styleWidth(spec)
  const px = spec.points.map((p) => {
    const x = timeToX(toUtcSeconds(p.ts))
    const y = priceToY(p.price)
    return x === null || y === null ? null : { x, y }
  })
  if (px.some((p) => p === null)) return null
  const handles = px as DrawingHandle[]

  const base: Omit<DrawingGeometry, 'segments'> = {
    id: spec.id,
    kind: spec.kind,
    handles,
    color,
    width,
  }

  if (spec.kind === 'trendline') {
    if (handles.length < 2) return null
    return {
      ...base,
      segments: [{ x1: handles[0].x, y1: handles[0].y, x2: handles[1].x, y2: handles[1].y }],
    }
  }
  if (spec.kind === 'ray') {
    if (handles.length < 2) return null
    const far = computeRayFarPoint(
      handles[0].x,
      handles[0].y,
      handles[1].x,
      handles[1].y,
      mediaWidth,
      mediaHeight,
    )
    return { ...base, segments: [{ x1: handles[0].x, y1: handles[0].y, x2: far.x, y2: far.y }] }
  }
  // hline / vline / rect / fib: handles resolve now; segments land in phase 3.
  return { ...base, segments: [] }
}

// The renderer's canvas target — the minimal slice of fancy-canvas'
// `CanvasRenderingTarget2D` we use (same local-typing rationale as trendlines.ts).
interface MediaCoordinateScope {
  context: CanvasRenderingContext2D
  mediaSize: { width: number; height: number }
}
interface DrawingDrawTarget {
  useMediaCoordinateSpace(callback: (scope: MediaCoordinateScope) => void): void
}

function strokeGeometry(
  ctx: CanvasRenderingContext2D,
  g: DrawingGeometry,
  selected: boolean,
): void {
  ctx.save()
  ctx.strokeStyle = g.color
  ctx.lineWidth = selected ? g.width + 1 : g.width
  ctx.setLineDash([])
  for (const seg of g.segments) {
    ctx.beginPath()
    ctx.moveTo(seg.x1, seg.y1)
    ctx.lineTo(seg.x2, seg.y2)
    ctx.stroke()
  }
  ctx.restore()
  // Endpoint handles on the selected drawing (a filled square with a white
  // border, so a drag target is obvious over any candle).
  if (selected) {
    for (const h of g.handles) {
      ctx.save()
      ctx.fillStyle = g.color
      ctx.strokeStyle = '#ffffff'
      ctx.lineWidth = 1.5
      ctx.beginPath()
      ctx.rect(h.x - HANDLE_HALF_PX, h.y - HANDLE_HALF_PX, HANDLE_HALF_PX * 2, HANDLE_HALF_PX * 2)
      ctx.fill()
      ctx.stroke()
      ctx.restore()
    }
  }
}

class DrawingPaneRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly primitive: DrawingPrimitive) {}

  draw(target: DrawingDrawTarget): void {
    target.useMediaCoordinateSpace((scope) => {
      // Compute geometry HERE, where the media size (needed for ray extension) is
      // live; the primitive caches it so the edit engine hit-tests exactly what's
      // painted.
      const { geometry, preview, selectedId } = this.primitive.renderGeometry(
        scope.mediaSize.width,
        scope.mediaSize.height,
      )
      const ctx = scope.context
      for (const g of geometry) strokeGeometry(ctx, g, g.id === selectedId)
      // The in-progress placement/drag preview draws dashed over the committed set.
      if (preview !== null) {
        ctx.save()
        ctx.globalAlpha = 0.8
        ctx.setLineDash([6, 4])
        ctx.strokeStyle = preview.color
        ctx.lineWidth = preview.width
        for (const seg of preview.segments) {
          ctx.beginPath()
          ctx.moveTo(seg.x1, seg.y1)
          ctx.lineTo(seg.x2, seg.y2)
          ctx.stroke()
        }
        ctx.restore()
      }
    })
  }
}

class DrawingPaneView implements IPrimitivePaneView {
  constructor(private readonly primitive: DrawingPrimitive) {}

  zOrder(): PrimitivePaneViewZOrder {
    return 'top'
  }

  renderer(): IPrimitivePaneRenderer {
    return new DrawingPaneRenderer(this.primitive)
  }
}

/**
 * The series primitive the chart attaches once and feeds drawings / selection /
 * preview into (the trendline-primitive lifecycle discipline: attached in the
 * chart-creation effect so it rides the live series and is disposed by
 * `chart.remove()`). Computes pixel geometry from the current chart + candle
 * series, caching the last computation so the edit engine can hit-test against
 * exactly what was drawn.
 */
export class DrawingPrimitive implements ISeriesPrimitive<Time> {
  private specs: ReadonlyArray<DrawingSpec> = []
  private previewSpec: DrawingSpec | null = null
  private selectedId: string | null = null
  private visible = true
  private defaultColor = DEFAULT_DRAWING_COLOR
  private chart: SeriesAttachedParameter<Time>['chart'] | null = null
  private series: SeriesAttachedParameter<Time>['series'] | null = null
  private requestUpdate: (() => void) | null = null
  private readonly paneView = new DrawingPaneView(this)
  // Last computed pixel geometry, refreshed every draw; the hit-test reads it so
  // it matches exactly what's on screen (empty until first drawn / attached).
  private cachedGeometry: DrawingGeometry[] = []
  private cachedPreview: DrawingGeometry | null = null

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
    if (!this.visible) return []
    return this.specs.length > 0 || this.previewSpec !== null ? [this.paneView] : []
  }

  setDrawings(specs: ReadonlyArray<DrawingSpec>): void {
    this.specs = specs
    this.requestUpdate?.()
  }

  setPreview(spec: DrawingSpec | null): void {
    this.previewSpec = spec
    this.requestUpdate?.()
  }

  setSelectedId(id: string | null): void {
    if (this.selectedId === id) return
    this.selectedId = id
    this.requestUpdate?.()
  }

  setVisible(visible: boolean): void {
    this.visible = visible
    this.requestUpdate?.()
  }

  /** Set the fallback stroke colour used when a drawing has no `style.color`
   * (user vs agent default; Plan 0097 phase 4 feeds the agent hue). */
  setDefaultColor(color: string): void {
    this.defaultColor = color
    this.requestUpdate?.()
  }

  selectedDrawingId(): string | null {
    return this.selectedId
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

  /** Recompute pixel geometry for every spec (+ preview) against the current
   * chart at the given media size, cache it, and return it for the renderer. The
   * cache is what the hit-test reads, so it matches exactly what was painted. */
  renderGeometry(
    width: number,
    height: number,
  ): { geometry: DrawingGeometry[]; preview: DrawingGeometry | null; selectedId: string | null } {
    const conv = this.converters()
    if (conv === null) {
      this.cachedGeometry = []
      this.cachedPreview = null
    } else {
      const { timeToX, priceToY } = conv
      this.cachedGeometry = this.specs
        .map((spec) =>
          computeDrawingGeometry(spec, timeToX, priceToY, width, height, this.defaultColor),
        )
        .filter((g): g is DrawingGeometry => g !== null)
      this.cachedPreview =
        this.previewSpec === null
          ? null
          : computeDrawingGeometry(
              this.previewSpec,
              timeToX,
              priceToY,
              width,
              height,
              this.defaultColor,
            )
    }
    return {
      geometry: this.cachedGeometry,
      preview: this.cachedPreview,
      selectedId: this.selectedId,
    }
  }

  /** The last-painted committed geometry (the hit-test surface). Empty until the
   * primitive has drawn once (attached + a paint). */
  currentGeometry(): DrawingGeometry[] {
    return this.cachedGeometry
  }

  /** The id of the committed drawing whose nearest segment is within
   * `tolerance` px of `(x,y)`, or `null`. Later drawings win ties (drawn last,
   * so on top). Returns `null` while hidden. */
  hitTestDrawingId(x: number, y: number, tolerance = DRAWING_HIT_TOLERANCE_PX): string | null {
    if (!this.visible) return null
    const geometry = this.currentGeometry()
    let best: string | null = null
    let bestDist = tolerance
    for (const g of geometry) {
      for (const seg of g.segments) {
        const d = pointSegmentDistance(x, y, seg.x1, seg.y1, seg.x2, seg.y2)
        if (d <= bestDist) {
          bestDist = d
          best = g.id
        }
      }
    }
    return best
  }

  /** The index of the handle of drawing `id` within `tolerance` px of `(x,y)`,
   * or `null`. Used to begin an endpoint drag on the selected drawing. */
  hitTestHandle(
    id: string,
    x: number,
    y: number,
    tolerance = HANDLE_HIT_TOLERANCE_PX,
  ): number | null {
    if (!this.visible) return null
    const g = this.currentGeometry().find((entry) => entry.id === id)
    if (g === undefined) return null
    let best: number | null = null
    let bestDist = tolerance
    g.handles.forEach((h, i) => {
      const d = Math.hypot(x - h.x, y - h.y)
      if (d <= bestDist) {
        bestDist = d
        best = i
      }
    })
    return best
  }

  /** lightweight-charts' primitive hover hook: reports a pointer cursor over any
   * drawing so the user knows it's grabbable. */
  hitTest(x: number, y: number): PrimitiveHoveredItem | null {
    if (!this.visible) return null
    const id = this.hitTestDrawingId(x, y)
    if (id === null) return null
    return { externalId: `drawing:${id}`, zOrder: 'top', cursorStyle: 'pointer' }
  }
}
