/**
 * User/agent freeform drawings → a lightweight-charts series primitive that
 * strokes `DrawingSpec` geometry anchored at `(time, price)` (Plan 0097, ADR-0091).
 *
 * Modelled on `trendlines.ts` (the pattern-trendline primitive) but for the
 * editable, user-authored drawing layer: it renders the eleven drawing kinds
 * (Plan 0097's six geometry kinds + Plan 0104's two position boxes and three range
 * measures), marks the SELECTED drawing with endpoint handles, and hit-tests
 * drawings + handles so the edit engine (`useDrawingTools`) can select and drag. It
 * reuses the pure ADR-0059 logical-coordinate helpers (`resolveTimeX`) and
 * `pointSegmentDistance` from `trendlines.ts` — a projected/off-grid anchor (a `ray`
 * past the last bar) still maps rather than being dropped.
 *
 * The pixel math lives in the pure, canvas-free `computeDrawingGeometry` /
 * `computeRayFarPoint` so it is unit-testable without a real chart. An agent-placed
 * position renders with an advisory label (ADR-0029/0099); its rationale surfaces
 * on hover via the chart tooltip.
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
import { t } from './i18n'
import { FIB_ANCHOR_COLOR, fibLevelColor } from './overlays'
import { riskReward } from './positions'
import { pointSegmentDistance, resolveTimeX } from './trendlines'

/** Default stroke colour for a user drawing when its `style.color` is unset. A
 * mid blue that reads on both light and dark themes. */
export const DEFAULT_DRAWING_COLOR = '#2962ff'
/** Colour of an agent-placed drawing when unstyled (Plan 0097 phase 4) — a warm
 * amber, distinct from the user default so provenance reads at a glance. */
export const DEFAULT_AGENT_DRAWING_COLOR = '#f08c00'
/** Default stroke width (px) when `style.width` is unset. */
export const DEFAULT_DRAWING_WIDTH = 2

/** Position-box zone colours (Plan 0104): the stop leg is risk (red), the target
 * leg is reward (green) — the standard long/short-tool palette, legible in both
 * themes. */
export const POSITION_STOP_COLOR = '#e03131'
export const POSITION_TARGET_COLOR = '#2f9e44'
/** Fill opacity for a position's stop/target zones — low enough candles read through. */
export const POSITION_FILL_ALPHA = 0.1

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
  // Plan 0104: a position is anchored by its single entry point (stop/target are
  // prices, not anchors); a range measure spans two anchors.
  long_position: 1,
  short_position: 1,
  date_range: 2,
  price_range: 2,
  date_price_range: 2,
}

/** Kinds whose anchors are placed at the RAW cursor price rather than snapped to a
 * bar's OHLC (Plan 0104 smoke follow-up): a position's entry/stop/target and the
 * range measures place anywhere on the price axis — a stop belongs at a chosen
 * price, not a candle's OHLC. The time axis still snaps to a bar so the anchor keeps
 * a real timestamp. The 0097 line tools keep the OHLC magnet. */
export const FREE_PRICE_KINDS: ReadonlySet<DrawingKind> = new Set<DrawingKind>([
  'long_position',
  'short_position',
  'date_range',
  'price_range',
  'date_price_range',
])

export function isFreePriceKind(kind: DrawingKind): boolean {
  return FREE_PRICE_KINDS.has(kind)
}

function toUtcSeconds(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp
}

/** A stroked line segment in media (pixel) coordinates. An optional `label`
 * (the fib-ratio caption) is drawn at the segment's left end. An optional
 * `color` overrides the drawing's stroke for THIS segment (the per-level fib
 * palette uses it); segments without it fall back to the drawing's `color`. */
export interface DrawingSegment {
  x1: number
  y1: number
  x2: number
  y2: number
  label?: string
  color?: string
}

/** An endpoint handle position in media (pixel) coordinates. */
export interface DrawingHandle {
  x: number
  y: number
}

/** A filled polygon zone with its own colour + opacity (Plan 0104): a position box
 * has two — the red stop leg and the green target leg — so a single `fillPolygon`
 * (one colour) no longer suffices. */
export interface DrawingFill {
  polygon: ReadonlyArray<{ x: number; y: number }>
  color: string
  alpha: number
}

/** The pixel geometry of one drawing: the stroked line(s), the anchor handles a
 * drag grabs, the resolved stroke style, an optional single `fillPolygon` (a
 * `rect` zone, drawn in the drawing colour) and optional multi-colour `fills` (a
 * position box's two zones). `segments` is empty when an anchor maps off-screen (a
 * converter returns `null`). */
export interface DrawingGeometry {
  id: string
  kind: DrawingKind
  segments: DrawingSegment[]
  handles: DrawingHandle[]
  color: string
  width: number
  fillPolygon?: ReadonlyArray<{ x: number; y: number }>
  fills?: ReadonlyArray<DrawingFill>
}

/** The standard Fibonacci retracement ratios (Plan 0097 phase 3): 0 / 23.6 /
 * 38.2 / 50 / 61.8 / 100 %, drawn between the two anchor prices. */
export const FIB_LEVELS: ReadonlyArray<number> = [0, 0.236, 0.382, 0.5, 0.618, 1.0]

/** The per-level colour a fib retracement line draws in — reusing the
 * analysis-side palette (`FIB_LEVEL_COLORS` / `FIB_ANCHOR_COLOR`, Plan 0105 /
 * ADR-0100 rule 2) so the drawing-dock fib and the `fibonacci` overlay read
 * alike. The 0 / 1.0 endpoints are the swing-anchor boundaries → neutral slate
 * (the grid's frame); the interior ratios get the graded palette, with the
 * watched 0.5 / 0.618 golden pocket landing on its distinct green/teal pair.
 * `String(r)` matches the map's keys for the non-integer interior ratios
 * (`0.236`/`0.382`/`0.5`/`0.618`). */
function fibSegmentColor(ratio: number): string {
  if (ratio === 0 || ratio === 1) return FIB_ANCHOR_COLOR
  return fibLevelColor(String(ratio))
}

/** Fill opacity for a `rect` zone — low enough the candles stay legible. */
export const RECT_FILL_ALPHA = 0.12

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

/** A visible fallback coordinate for the off-axis handle of an hline/vline whose
 * anchor's other coordinate maps off-screen — kept inside the pane so the handle
 * stays grabbable. */
function clampVisible(extent: number): number {
  return Math.min(Math.max(8, extent * 0.15), Math.max(8, extent - 8))
}

/** Count of bars whose time falls within `[tsA, tsB]` inclusive — the bar-span
 * readout for the date range measures (Plan 0104). */
function barsBetween(
  barTimes: readonly UTCTimestamp[],
  tsA: UTCTimestamp,
  tsB: UTCTimestamp,
): number {
  const lo = Math.min(tsA, tsB)
  const hi = Math.max(tsA, tsB)
  let count = 0
  for (const time of barTimes) if (time >= lo && time <= hi) count += 1
  return count
}

/** Localised calendar-span readout from two ISO anchor times: days when the span
 * is >= 1 day, else hours (Plan 0104; en-US numerals per ADR-0063). */
function timeSpanReadout(tsA: string, tsB: string): string {
  const ms = Math.abs(new Date(tsB).getTime() - new Date(tsA).getTime())
  const hours = ms / 3_600_000
  if (hours >= 24) return t('chart.draw.readout.days', { n: Number((hours / 24).toFixed(1)) })
  return t('chart.draw.readout.hours', { n: Number(hours.toFixed(1)) })
}

/** Localised price-move readout `Δ <abs> (<±pct>%)` between two anchor prices
 * (Plan 0104; en-US numerals per ADR-0063). */
function priceDeltaReadout(priceA: number, priceB: number): string {
  const delta = priceB - priceA
  const abs = Math.abs(delta).toLocaleString('en-US', { maximumFractionDigits: 2 })
  const pct = priceA !== 0 ? (delta / priceA) * 100 : 0
  const pctStr = (pct >= 0 ? '+' : '') + pct.toLocaleString('en-US', { maximumFractionDigits: 2 })
  return t('chart.draw.readout.priceDelta', { value: abs, pct: pctStr })
}

/**
 * Pure: map one `DrawingSpec` to its pixel geometry via a time→x converter (with
 * the off-grid `resolveTimeX` fallback) and a price→y converter. Covers all six
 * kinds: `trendline` (segment), `ray` (extended to the edge), `hline`/`vline`
 * (full-axis line, only the relevant coordinate must map), `rect` (four edges +
 * a fill zone), `fib` (the standard retracement grid, extended right). Returns
 * `null` only when a *defining* anchor coordinate maps off-screen.
 * `mediaWidth`/`mediaHeight` bound the extend-to-edge kinds.
 */
export function computeDrawingGeometry(
  spec: DrawingSpec,
  timeToX: (t: UTCTimestamp) => number | null,
  priceToY: (price: number) => number | null,
  mediaWidth: number,
  mediaHeight: number,
  defaultColor?: string,
  barTimes: readonly UTCTimestamp[] = [],
): DrawingGeometry | null {
  // Unstyled drawings colour by provenance: user blue, agent amber (so the two
  // sources read apart at a glance, ADR-0091). An explicit `defaultColor`
  // overrides (used by tests).
  const fallback =
    defaultColor ??
    (spec.provenance === 'agent' ? DEFAULT_AGENT_DRAWING_COLOR : DEFAULT_DRAWING_COLOR)
  const color = styleColor(spec, fallback)
  const width = styleWidth(spec)
  const style = { id: spec.id, kind: spec.kind, color, width }

  // hline / vline anchor by a SINGLE coordinate; the line spans the full axis, so
  // the off-axis coordinate need not map (the handle falls back into view).
  if (spec.kind === 'hline') {
    const y = priceToY(spec.points[0].price)
    if (y === null) return null
    const xa = timeToX(toUtcSeconds(spec.points[0].ts))
    const hx = xa ?? clampVisible(mediaWidth)
    return {
      ...style,
      handles: [{ x: hx, y }],
      segments: [{ x1: 0, y1: y, x2: mediaWidth, y2: y }],
    }
  }
  if (spec.kind === 'vline') {
    const x = timeToX(toUtcSeconds(spec.points[0].ts))
    if (x === null) return null
    const ya = priceToY(spec.points[0].price)
    const hy = ya ?? clampVisible(mediaHeight)
    return {
      ...style,
      handles: [{ x, y: hy }],
      segments: [{ x1: x, y1: 0, x2: x, y2: mediaHeight }],
    }
  }

  // Position box (Plan 0104): one entry anchor + stop/target prices. Extends right
  // from the entry time (like a ray of zones): a red entry↔stop leg and a green
  // entry↔target leg, three price handles (entry, stop, target), an R:R caption.
  if (spec.kind === 'long_position' || spec.kind === 'short_position') {
    if (spec.stop == null || spec.target == null) return null // malformed (pre-validated away)
    const entryY = priceToY(spec.points[0].price)
    const stopY = priceToY(spec.stop)
    const targetY = priceToY(spec.target)
    if (entryY === null || stopY === null || targetY === null) return null
    const entryX = timeToX(toUtcSeconds(spec.points[0].ts)) ?? clampVisible(mediaWidth)
    const right = mediaWidth
    const rr = riskReward(spec.points[0].price, spec.stop, spec.target)
    const rrLabel = t('chart.draw.readout.riskReward', {
      rr: rr === null ? '—' : rr.toFixed(2),
    })
    // An agent-placed position is a recommendation made visual — label it advisory
    // (ADR-0029/0099); the rationale rides the hover tooltip, not the canvas.
    const entryLabel =
      spec.provenance === 'agent' ? `${t('chart.draw.advisory')} · ${rrLabel}` : rrLabel
    const zone = (y: number): ReadonlyArray<{ x: number; y: number }> => [
      { x: entryX, y: entryY },
      { x: right, y: entryY },
      { x: right, y },
      { x: entryX, y },
    ]
    return {
      ...style,
      handles: [
        { x: entryX, y: entryY },
        { x: entryX, y: stopY },
        { x: entryX, y: targetY },
      ],
      segments: [
        { x1: entryX, y1: entryY, x2: right, y2: entryY, label: entryLabel },
        { x1: entryX, y1: stopY, x2: right, y2: stopY, color: POSITION_STOP_COLOR },
        { x1: entryX, y1: targetY, x2: right, y2: targetY, color: POSITION_TARGET_COLOR },
        { x1: entryX, y1: stopY, x2: entryX, y2: targetY },
      ],
      fills: [
        { polygon: zone(stopY), color: POSITION_STOP_COLOR, alpha: POSITION_FILL_ALPHA },
        { polygon: zone(targetY), color: POSITION_TARGET_COLOR, alpha: POSITION_FILL_ALPHA },
      ],
    }
  }

  // Date range (Plan 0104): two vertical lines at the anchor times + a readout
  // (bar count · calendar span) on a connector at mid-height. Prices are carried
  // by the anchors but not used (the lines span the full height).
  if (spec.kind === 'date_range') {
    const xa = timeToX(toUtcSeconds(spec.points[0].ts))
    const xb = timeToX(toUtcSeconds(spec.points[1].ts))
    if (xa === null || xb === null) return null
    const bars = barsBetween(
      barTimes,
      toUtcSeconds(spec.points[0].ts),
      toUtcSeconds(spec.points[1].ts),
    )
    const label = `${t('chart.draw.readout.bars', { n: bars })} · ${timeSpanReadout(
      spec.points[0].ts,
      spec.points[1].ts,
    )}`
    const midY = mediaHeight / 2
    const handleY = priceToY(spec.points[0].price) ?? clampVisible(mediaHeight)
    const handleYb = priceToY(spec.points[1].price) ?? clampVisible(mediaHeight)
    return {
      ...style,
      handles: [
        { x: xa, y: handleY },
        { x: xb, y: handleYb },
      ],
      segments: [
        { x1: xa, y1: 0, x2: xa, y2: mediaHeight },
        { x1: xb, y1: 0, x2: xb, y2: mediaHeight },
        { x1: Math.min(xa, xb), y1: midY, x2: Math.max(xa, xb), y2: midY, label },
      ],
    }
  }

  // Price range (Plan 0104): two horizontal lines at the anchor prices + a readout
  // (Δprice, %) on a connector at mid-width. Times are carried but not used.
  if (spec.kind === 'price_range') {
    const ya = priceToY(spec.points[0].price)
    const yb = priceToY(spec.points[1].price)
    if (ya === null || yb === null) return null
    const label = priceDeltaReadout(spec.points[0].price, spec.points[1].price)
    const midX = mediaWidth / 2
    const handleX = timeToX(toUtcSeconds(spec.points[0].ts)) ?? clampVisible(mediaWidth)
    const handleXb = timeToX(toUtcSeconds(spec.points[1].ts)) ?? clampVisible(mediaWidth)
    return {
      ...style,
      handles: [
        { x: handleX, y: ya },
        { x: handleXb, y: yb },
      ],
      segments: [
        { x1: 0, y1: ya, x2: mediaWidth, y2: ya },
        { x1: 0, y1: yb, x2: mediaWidth, y2: yb },
        { x1: midX, y1: Math.min(ya, yb), x2: midX, y2: Math.max(ya, yb), label },
      ],
    }
  }

  // The remaining kinds are two-anchor; both anchors must map fully.
  const a = mapPoint(spec.points[0], timeToX, priceToY)
  const b = mapPoint(spec.points[1], timeToX, priceToY)
  if (a === null || b === null) return null
  const handles = [a, b]

  if (spec.kind === 'trendline') {
    return { ...style, handles, segments: [{ x1: a.x, y1: a.y, x2: b.x, y2: b.y }] }
  }
  if (spec.kind === 'ray') {
    const far = computeRayFarPoint(a.x, a.y, b.x, b.y, mediaWidth, mediaHeight)
    return { ...style, handles, segments: [{ x1: a.x, y1: a.y, x2: far.x, y2: far.y }] }
  }
  if (spec.kind === 'rect') {
    const corners = [
      { x: a.x, y: a.y },
      { x: b.x, y: a.y },
      { x: b.x, y: b.y },
      { x: a.x, y: b.y },
    ]
    const segments: DrawingSegment[] = corners.map((c, i) => {
      const n = corners[(i + 1) % corners.length]
      return { x1: c.x, y1: c.y, x2: n.x, y2: n.y }
    })
    return { ...style, handles, segments, fillPolygon: corners }
  }
  if (spec.kind === 'date_price_range') {
    // A measured box: the four edges + a fill, captioned with BOTH readouts (bar
    // span · calendar span, then Δprice · %).
    const corners = [
      { x: a.x, y: a.y },
      { x: b.x, y: a.y },
      { x: b.x, y: b.y },
      { x: a.x, y: b.y },
    ]
    const bars = barsBetween(
      barTimes,
      toUtcSeconds(spec.points[0].ts),
      toUtcSeconds(spec.points[1].ts),
    )
    const label = `${t('chart.draw.readout.bars', { n: bars })} · ${timeSpanReadout(
      spec.points[0].ts,
      spec.points[1].ts,
    )} · ${priceDeltaReadout(spec.points[0].price, spec.points[1].price)}`
    const segments: DrawingSegment[] = corners.map((c, i) => {
      const n = corners[(i + 1) % corners.length]
      const seg: DrawingSegment = { x1: c.x, y1: c.y, x2: n.x, y2: n.y }
      if (i === 0) seg.label = label // caption on the top edge
      return seg
    })
    return { ...style, handles, segments, fillPolygon: corners }
  }
  // fib: horizontal lines at the standard ratios between the two anchor PRICES,
  // spanning from the left anchor to the right edge, each captioned with its ratio.
  // Unstyled, each level draws in its own palette colour (legible grid); an
  // explicit user `style.color` wins and collapses the grid back to one colour.
  const xLeft = Math.min(a.x, b.x)
  const perLevel = spec.style?.color == null
  const segments: DrawingSegment[] = FIB_LEVELS.map((r) => {
    const y = a.y + r * (b.y - a.y)
    const seg: DrawingSegment = {
      x1: xLeft,
      y1: y,
      x2: mediaWidth,
      y2: y,
      label: `${(r * 100).toFixed(1)}%`,
    }
    if (perLevel) seg.color = fibSegmentColor(r)
    return seg
  })
  return { ...style, handles, segments }
}

function mapPoint(
  p: { ts: string; price: number },
  timeToX: (t: UTCTimestamp) => number | null,
  priceToY: (price: number) => number | null,
): DrawingHandle | null {
  const x = timeToX(toUtcSeconds(p.ts))
  const y = priceToY(p.price)
  return x === null || y === null ? null : { x, y }
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
  // A rect zone's fill sits beneath every stroke, low-alpha so candles read through.
  if (g.fillPolygon !== undefined && g.fillPolygon.length >= 3) {
    ctx.save()
    ctx.globalAlpha = RECT_FILL_ALPHA
    ctx.fillStyle = g.color
    ctx.beginPath()
    ctx.moveTo(g.fillPolygon[0].x, g.fillPolygon[0].y)
    for (const p of g.fillPolygon.slice(1)) ctx.lineTo(p.x, p.y)
    ctx.closePath()
    ctx.fill()
    ctx.restore()
  }
  // Multi-colour zones (a position box's red stop leg + green target leg), each in
  // its own colour + opacity, beneath the strokes.
  for (const fill of g.fills ?? []) {
    if (fill.polygon.length < 3) continue
    ctx.save()
    ctx.globalAlpha = fill.alpha
    ctx.fillStyle = fill.color
    ctx.beginPath()
    ctx.moveTo(fill.polygon[0].x, fill.polygon[0].y)
    for (const p of fill.polygon.slice(1)) ctx.lineTo(p.x, p.y)
    ctx.closePath()
    ctx.fill()
    ctx.restore()
  }
  ctx.save()
  ctx.lineWidth = selected ? g.width + 1 : g.width
  ctx.setLineDash([])
  for (const seg of g.segments) {
    // A per-segment colour (the fib palette) overrides the drawing's stroke;
    // every other kind leaves it unset and draws in `g.color`.
    const segColor = seg.color ?? g.color
    ctx.strokeStyle = segColor
    ctx.beginPath()
    ctx.moveTo(seg.x1, seg.y1)
    ctx.lineTo(seg.x2, seg.y2)
    ctx.stroke()
    // A fib line's ratio caption, above its left end, in its own line colour.
    if (seg.label !== undefined) {
      ctx.save()
      ctx.fillStyle = segColor
      ctx.font = '10px sans-serif'
      ctx.textBaseline = 'bottom'
      ctx.fillText(seg.label, seg.x1 + 2, seg.y1 - 1)
      ctx.restore()
    }
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

  selectedDrawingId(): string | null {
    return this.selectedId
  }

  private converters(): {
    timeToX: (t: UTCTimestamp) => number | null
    priceToY: (price: number) => number | null
    barTimes: UTCTimestamp[]
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
    return { timeToX, priceToY, barTimes }
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
      const { timeToX, priceToY, barTimes } = conv
      this.cachedGeometry = this.specs
        .map((spec) =>
          computeDrawingGeometry(spec, timeToX, priceToY, width, height, undefined, barTimes),
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
              undefined,
              barTimes,
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

  /** The committed drawing under `(x,y)`, or `null` — the tooltip reads a hovered
   * agent position's rationale off it (Plan 0104). Returns `null` while hidden. */
  hoveredDrawingSpec(x: number, y: number): DrawingSpec | null {
    const id = this.hitTestDrawingId(x, y)
    if (id === null) return null
    return this.specs.find((spec) => spec.id === id) ?? null
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
