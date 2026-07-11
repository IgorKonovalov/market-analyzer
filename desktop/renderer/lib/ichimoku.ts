/**
 * Ichimoku Kinkō Hyō chart overlay (Plan 0073 phase 4, ADR-0067) → a
 * lightweight-charts series primitive that strokes the five lines (Tenkan, Kijun,
 * Senkou A/B, Chikou) and fills the cloud between the two displaced Senkou spans.
 * Modelled on `trendlines.ts`: the primitive rides the chart's own coordinate
 * system and tracks pan/zoom for free.
 *
 * Ichimoku is the only overlay whose plotted position differs from its computed
 * bar (ADR-0067): Senkou A/B are projected `displacement` bars into the FUTURE
 * (past the last candle), Chikou is lagged `displacement` bars into the PAST. So
 * unlike ema/sma/supertrend — drawn as on-grid line SERIES — Ichimoku is a
 * primitive that maps each point through the LOGICAL index scale
 * (`timeScale().logicalToCoordinate`): a value computed at bar `i` plots at
 * logical `i` (Tenkan/Kijun), `i + displacement` (Senkou), or `i - displacement`
 * (Chikou). `logicalToCoordinate` extrapolates for out-of-range logicals (the
 * same mechanism the trendline primitive uses for projected necklines,
 * ADR-0059), so points past the last candle still resolve rather than drop.
 *
 * The client-side compute is a faithful mirror of
 * `src/market_analyser/analysis/indicators.py::ichimoku` — display-only, outside
 * the determinism-critical decision path (the sidecar copy feeds the trend
 * classifier). The pure geometry + cloud-region math live in canvas-free
 * functions so they are unit-tested without a real chart.
 */
import type {
  ISeriesPrimitive,
  ISeriesPrimitivePaneRenderer,
  ISeriesPrimitivePaneView,
  Logical,
  SeriesAttachedParameter,
  SeriesPrimitivePaneViewZOrder,
  Time,
} from 'lightweight-charts'

import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'

/** Classic Ichimoku periods (9/26/52/26) — mirror the Python defaults. The
 * renderer applies these when the overlay descriptor omits a field. */
export const ICHIMOKU_DEFAULTS = {
  conversion: 9,
  base: 26,
  spanB: 52,
  displacement: 26,
} as const

export const ICHIMOKU_LAYER_LABEL = 'Ichimoku'

/** Theme-resolved Ichimoku palette (resolved from the DOM tokens by the caller,
 * passed into the primitive — lightweight-charts/canvas can't resolve `var()`). */
export interface IchimokuColors {
  tenkan: string
  kijun: string
  chikou: string
  /** Line stroke of the leading spans A / B. */
  spanA: string
  spanB: string
  /** Cloud fill where Senkou A > B (bull) / A < B (bear); translucent. */
  cloudBull: string
  cloudBear: string
}

// Light-theme fallbacks for when a token is unset — e.g. in jsdom unit tests
// where styles.css isn't loaded. At runtime the `--ichimoku-*` tokens win.
export const ICHIMOKU_COLOR_FALLBACK: IchimokuColors = {
  tenkan: '#2563eb',
  kijun: '#b91c1c',
  chikou: '#7c3aed',
  spanA: '#16a34a',
  spanB: '#dc2626',
  cloudBull: 'rgba(22, 163, 74, 0.18)',
  cloudBear: 'rgba(220, 38, 38, 0.18)',
}

/** Resolve the Ichimoku palette off the themed DOM, falling back to the light
 * defaults for any unset token. */
export function readIchimokuColors(el: HTMLElement): IchimokuColors {
  const c = getComputedStyle(el)
  const v = (name: string, fallback: string): string => c.getPropertyValue(name).trim() || fallback
  return {
    tenkan: v('--ichimoku-tenkan', ICHIMOKU_COLOR_FALLBACK.tenkan),
    kijun: v('--ichimoku-kijun', ICHIMOKU_COLOR_FALLBACK.kijun),
    chikou: v('--ichimoku-chikou', ICHIMOKU_COLOR_FALLBACK.chikou),
    spanA: v('--ichimoku-span-a', ICHIMOKU_COLOR_FALLBACK.spanA),
    spanB: v('--ichimoku-span-b', ICHIMOKU_COLOR_FALLBACK.spanB),
    cloudBull: v('--ichimoku-cloud-bull', ICHIMOKU_COLOR_FALLBACK.cloudBull),
    cloudBear: v('--ichimoku-cloud-bear', ICHIMOKU_COLOR_FALLBACK.cloudBear),
  }
}

/** One Ichimoku reading, every field AS COMPUTED at bar `i` from `bars[0..=i]` —
 * displacement is applied when plotting, not baked in (ADR-0067). */
export interface IchimokuValue {
  tenkan: number
  kijun: number
  senkouA: number
  senkouB: number
  chikou: number
}

/** Resolve the four periods from an overlay spec, applying the classic defaults
 * for any omitted field. */
export function ichimokuPeriods(spec: OverlaySpec): {
  conversion: number
  base: number
  spanB: number
  displacement: number
} {
  return {
    conversion: spec.conversion ?? ICHIMOKU_DEFAULTS.conversion,
    base: spec.base ?? ICHIMOKU_DEFAULTS.base,
    spanB: spec.span_b ?? ICHIMOKU_DEFAULTS.spanB,
    displacement: spec.displacement ?? ICHIMOKU_DEFAULTS.displacement,
  }
}

/** Midpoint of the highest high and lowest low over the trailing `period` bars
 * inclusive of bar `i` (the shared Ichimoku line convention). */
function hlMidpoint(bars: ReadonlyArray<Bar>, i: number, period: number): number {
  let hi = -Infinity
  let lo = Infinity
  for (let j = i - period + 1; j <= i; j += 1) {
    if (bars[j].high > hi) hi = bars[j].high
    if (bars[j].low < lo) lo = bars[j].low
  }
  return (hi + lo) / 2
}

/**
 * Client-side Ichimoku, a faithful mirror of the Python `ichimoku`. Returns one
 * entry per bar; `null` until every component is defined (the widest trailing
 * window, `max(conversion, base, spanB) - 1`). Displacement is NOT applied here —
 * it is a plotting concern (see `computeIchimokuGeometry`). `[]` on invalid
 * periods.
 */
export function computeIchimoku(
  bars: ReadonlyArray<Bar>,
  conversion: number = ICHIMOKU_DEFAULTS.conversion,
  base: number = ICHIMOKU_DEFAULTS.base,
  spanB: number = ICHIMOKU_DEFAULTS.spanB,
): Array<IchimokuValue | null> {
  if (conversion < 1 || base < 1 || spanB < 1) return []
  const n = bars.length
  const out: Array<IchimokuValue | null> = new Array(n).fill(null)
  const definedFrom = Math.max(conversion, base, spanB) - 1
  for (let i = definedFrom; i < n; i += 1) {
    const tenkan = hlMidpoint(bars, i, conversion)
    const kijun = hlMidpoint(bars, i, base)
    out[i] = {
      tenkan,
      kijun,
      senkouA: (tenkan + kijun) / 2,
      senkouB: hlMidpoint(bars, i, spanB),
      chikou: bars[i].close,
    }
  }
  return out
}

/** A single plotted line point in (logical-index, price) space. */
export interface IchimokuLinePoint {
  logical: number
  value: number
}

/** A cloud sample: the two Senkou spans at one displaced logical position. */
export interface IchimokuCloudPoint {
  logical: number
  a: number
  b: number
}

/** The full plotted geometry, in logical-index space (canvas-free, unit-tested).
 * Senkou A/B and the cloud are displaced `+displacement`; Chikou is `-displacement`. */
export interface IchimokuGeometry {
  tenkan: IchimokuLinePoint[]
  kijun: IchimokuLinePoint[]
  spanA: IchimokuLinePoint[]
  spanB: IchimokuLinePoint[]
  chikou: IchimokuLinePoint[]
  cloud: IchimokuCloudPoint[]
}

/**
 * Pure: map the computed Ichimoku series to plotted geometry in logical-index
 * space, applying displacement. A value computed at bar `i` plots at logical `i`
 * (Tenkan/Kijun), `i + displacement` (Senkou A/B + cloud), or `i - displacement`
 * (Chikou). This is the displaced mapping the phase-4 unit test pins.
 */
export function computeIchimokuGeometry(
  bars: ReadonlyArray<Bar>,
  spec: OverlaySpec,
): IchimokuGeometry {
  const { conversion, base, spanB, displacement } = ichimokuPeriods(spec)
  const series = computeIchimoku(bars, conversion, base, spanB)
  const geom: IchimokuGeometry = {
    tenkan: [],
    kijun: [],
    spanA: [],
    spanB: [],
    chikou: [],
    cloud: [],
  }
  for (let i = 0; i < series.length; i += 1) {
    const v = series[i]
    if (v === null) continue
    geom.tenkan.push({ logical: i, value: v.tenkan })
    geom.kijun.push({ logical: i, value: v.kijun })
    geom.spanA.push({ logical: i + displacement, value: v.senkouA })
    geom.spanB.push({ logical: i + displacement, value: v.senkouB })
    geom.cloud.push({ logical: i + displacement, a: v.senkouA, b: v.senkouB })
    geom.chikou.push({ logical: i - displacement, value: v.chikou })
  }
  return geom
}

/** A filled cloud region between two consecutive cloud samples: a polygon in
 * (logical, price) space plus the resolved fill colour. A crossover between the
 * samples is split into two regions meeting at the intersection (the cloud
 * colour flips green↔red at A-B crossovers). */
export interface IchimokuCloudRegion {
  points: Array<{ logical: number; value: number }>
  color: string
}

/** The cloud colour for a sample: bull (A > B) or bear (A <= B). */
export function cloudColorFor(a: number, b: number, colors: IchimokuColors): string {
  return a > b ? colors.cloudBull : colors.cloudBear
}

/**
 * Pure: build the filled cloud regions (canvas-free) between consecutive cloud
 * samples. Each interval is a trapezoid with corners `(L1,a1)-(L2,a2)` on top and
 * `(L2,b2)-(L1,b1)` back along the bottom, coloured by A-vs-B. When A and B cross
 * inside the interval, the trapezoid is split at the intersection into two
 * triangles of opposite colour — so the cloud flips green↔red exactly at the
 * crossover. Pins the colour rule + crossover split for the phase-4 unit test.
 */
export function computeCloudRegions(
  cloud: ReadonlyArray<IchimokuCloudPoint>,
  colors: IchimokuColors,
): IchimokuCloudRegion[] {
  const regions: IchimokuCloudRegion[] = []
  for (let i = 0; i + 1 < cloud.length; i += 1) {
    const p1 = cloud[i]
    const p2 = cloud[i + 1]
    const d1 = p1.a - p1.b
    const d2 = p2.a - p2.b
    if (d1 === 0 && d2 === 0) continue // degenerate flat overlap — nothing to fill
    // Same side (or one endpoint exactly on the line): one trapezoid, coloured by
    // whichever endpoint is off the line.
    if (d1 * d2 >= 0) {
      const color = cloudColorFor(d1 !== 0 ? p1.a : p2.a, d1 !== 0 ? p1.b : p2.b, colors)
      regions.push({
        points: [
          { logical: p1.logical, value: p1.a },
          { logical: p2.logical, value: p2.a },
          { logical: p2.logical, value: p2.b },
          { logical: p1.logical, value: p1.b },
        ],
        color,
      })
      continue
    }
    // Crossover: A and B swap order. Split at the intersection logical.
    const t = d1 / (d1 - d2)
    const crossLogical = p1.logical + t * (p2.logical - p1.logical)
    const crossValue = p1.a + t * (p2.a - p1.a) // == b at the crossing
    regions.push({
      points: [
        { logical: p1.logical, value: p1.a },
        { logical: crossLogical, value: crossValue },
        { logical: p1.logical, value: p1.b },
      ],
      color: cloudColorFor(p1.a, p1.b, colors),
    })
    regions.push({
      points: [
        { logical: crossLogical, value: crossValue },
        { logical: p2.logical, value: p2.a },
        { logical: p2.logical, value: p2.b },
      ],
      color: cloudColorFor(p2.a, p2.b, colors),
    })
  }
  return regions
}

// The renderer's canvas target — the minimal slice of fancy-canvas'
// `CanvasRenderingTarget2D` we use (same local-typing rationale as spans.ts /
// trendlines.ts).
interface MediaCoordinateScope {
  context: CanvasRenderingContext2D
  mediaSize: { width: number; height: number }
}
interface IchimokuDrawTarget {
  useMediaCoordinateSpace(callback: (scope: MediaCoordinateScope) => void): void
}

/** One stroked line in media (pixel) coordinates. */
interface PixelLine {
  points: Array<{ x: number; y: number }>
  color: string
}
/** One filled region in media (pixel) coordinates. */
interface PixelRegion {
  points: Array<{ x: number; y: number }>
  color: string
}

class IchimokuPaneRenderer implements ISeriesPrimitivePaneRenderer {
  constructor(
    private readonly fills: PixelRegion[],
    private readonly lines: PixelLine[],
  ) {}

  draw(target: IchimokuDrawTarget): void {
    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context
      // Cloud fill first (beneath the lines).
      for (const region of this.fills) {
        if (region.points.length < 3) continue
        ctx.save()
        ctx.fillStyle = region.color
        ctx.beginPath()
        ctx.moveTo(region.points[0].x, region.points[0].y)
        for (let i = 1; i < region.points.length; i += 1) {
          ctx.lineTo(region.points[i].x, region.points[i].y)
        }
        ctx.closePath()
        ctx.fill()
        ctx.restore()
      }
      for (const line of this.lines) {
        if (line.points.length < 2) continue
        ctx.save()
        ctx.strokeStyle = line.color
        ctx.lineWidth = 1.5
        ctx.beginPath()
        ctx.moveTo(line.points[0].x, line.points[0].y)
        for (let i = 1; i < line.points.length; i += 1) {
          ctx.lineTo(line.points[i].x, line.points[i].y)
        }
        ctx.stroke()
        ctx.restore()
      }
    })
  }
}

class IchimokuPaneView implements ISeriesPrimitivePaneView {
  constructor(private readonly primitive: IchimokuPrimitive) {}

  zOrder(): SeriesPrimitivePaneViewZOrder {
    // Above the candles' body but the translucent cloud reads fine over them; the
    // trendline primitive uses 'top' for the same reason (thin lines need to show).
    return 'top'
  }

  renderer(): ISeriesPrimitivePaneRenderer {
    const { fills, lines } = this.primitive.currentPixels()
    return new IchimokuPaneRenderer(fills, lines)
  }
}

/**
 * The series primitive the chart attaches once and feeds geometries/colours/
 * visibility into. `paneViews()` returns the view only while visible and
 * non-empty, so toggling the legend row or clearing the overlays removes all of
 * it; the chart recomputes pixels on every pan/zoom because it re-reads
 * `paneViews`. It maps each geometry's logical positions through
 * `logicalToCoordinate` (extrapolated for the displaced future/past points) and
 * each price through the candle series' `priceToCoordinate`.
 */
export class IchimokuPrimitive implements ISeriesPrimitive<Time> {
  private geometries: ReadonlyArray<IchimokuGeometry> = []
  private colors: IchimokuColors
  private visible = true
  private chart: SeriesAttachedParameter<Time>['chart'] | null = null
  private series: SeriesAttachedParameter<Time>['series'] | null = null
  private requestUpdate: (() => void) | null = null
  private readonly paneView = new IchimokuPaneView(this)

  constructor(colors: IchimokuColors) {
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
    return this.visible && this.geometries.length > 0 ? [this.paneView] : []
  }

  setGeometries(geometries: ReadonlyArray<IchimokuGeometry>): void {
    this.geometries = geometries
    this.requestUpdate?.()
  }

  setColors(colors: IchimokuColors): void {
    this.colors = colors
    this.requestUpdate?.()
  }

  setVisible(visible: boolean): void {
    this.visible = visible
    this.requestUpdate?.()
  }

  /** Current pixel fills + lines (media coords). Empty until attached. */
  currentPixels(): { fills: PixelRegion[]; lines: PixelLine[] } {
    const timeScale = this.chart?.timeScale()
    const series = this.series
    if (!timeScale || !series) return { fills: [], lines: [] }
    const xOf = (logical: number): number | null =>
      timeScale.logicalToCoordinate(logical as Logical)
    const yOf = (price: number): number | null => series.priceToCoordinate(price)

    const toPixels = (
      pts: ReadonlyArray<{ logical: number; value: number }>,
    ): Array<{ x: number; y: number }> => {
      const out: Array<{ x: number; y: number }> = []
      for (const p of pts) {
        const x = xOf(p.logical)
        const y = yOf(p.value)
        if (x === null || y === null) continue
        out.push({ x, y })
      }
      return out
    }

    const fills: PixelRegion[] = []
    const lines: PixelLine[] = []
    for (const geom of this.geometries) {
      for (const region of computeCloudRegions(geom.cloud, this.colors)) {
        const pts = toPixels(region.points)
        if (pts.length >= 3) fills.push({ points: pts, color: region.color })
      }
      lines.push({ points: toPixels(geom.spanA), color: this.colors.spanA })
      lines.push({ points: toPixels(geom.spanB), color: this.colors.spanB })
      lines.push({ points: toPixels(geom.tenkan), color: this.colors.tenkan })
      lines.push({ points: toPixels(geom.kijun), color: this.colors.kijun })
      lines.push({ points: toPixels(geom.chikou), color: this.colors.chikou })
    }
    return { fills, lines }
  }
}
