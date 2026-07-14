/**
 * Overlay registry (Plan 0029 phase 2). One table maps each supported overlay
 * `kind` to its line color and its indicator math, collapsing what used to be
 * four scattered edit sites in `CandlestickChart.tsx` (a supported-kinds set, a
 * color switch, a compute switch, and the reconcile loop) into a single entry.
 *
 * Adding a new overlay kind is now one registry row — the supported-kinds check
 * (`isSupportedOverlay`), the color (`overlayColorFor`), and the data
 * (`computeOverlayData`) all read from here. The chart's reconcile loop stays in
 * the component but consults these helpers.
 *
 * MVP scope is `ema` and `sma`; `supertrend`/`ichimoku`/`bbands` are additive
 * indicator kinds with their own dedicated draw paths. `rsi`/`macd` draw in their
 * own oscillator sub-panes (Plan 0091 phase 9, via `useOscillatorPanes`), so —
 * like the other oscillators — their registry `compute` returns `[]` and the
 * sub-pane hook owns their draw. (They were previously unrendered "log-and-skip"
 * kinds; the divergence work needed real RSI/MACD panes to draw onto.)
 */
import type { LineData, UTCTimestamp, WhitespaceData } from 'lightweight-charts'

import { computeEma, computeSma } from './indicators'
import { DEFAULT_MARKER_COLORS } from './markers'
import type { ChartLineElement } from './chartStyle'
import type { Bar } from '../types/sidecar/bar'
import type { OverlayKind, OverlaySpec } from '../types/events'

export interface OverlayDefinition {
  /** Fallback line color — used when the theme token is unset (and the value
   * asserted by non-DOM unit tests). The chart prefers `colorToken` at runtime. */
  color: string
  /** CSS custom property the chart resolves per theme (Plan 0033 phase 4). When
   * present and set, it overrides `color`; absent → `color` is used as-is. */
  colorToken?: string
  /** Trailing indicator math: value at index `i` uses `bars[0..=i]` only. Takes
   * the full `OverlaySpec` so multi-param overlays (supertrend: period +
   * multiplier) can read every parameter, not just `period`. */
  compute(bars: Bar[], spec: OverlaySpec): LineData[]
}

/** Supertrend default period / ATR multiplier — mirror the Python defaults in
 * `analysis/indicators.py::supertrend`. */
const SUPERTREND_DEFAULT_PERIOD = 10
const SUPERTREND_DEFAULT_MULTIPLIER = 3

/** Bollinger Bands line colour (Plan 0082 phase 2). A static violet used for all
 * three bands and the single legend swatch; `bbands` is not a user-styleable
 * element (ADR-0062), so it keeps this registry colour on both themes rather than
 * a per-theme token. Shared by the registry entry and `useBbandsSeries`. */
export const BBANDS_LINE_COLOR = '#8b5cf6'

/** Plan-0092 price-structure overlay colours (static, not user-styleable —
 * ADR-0062). Shared by the registry legend swatches and the draw hooks. */
export const FIB_LINE_COLOR = '#c084fc' // fibonacci grid — violet (legend swatch + unknown-ratio fallback)
export const PIVOT_LINE_COLOR = '#f59e0b' // classic pivots — amber (legend swatch + unknown-level fallback)
export const ANCHORED_VWAP_COLOR = '#14b8a6' // anchored VWAP — teal

/** Per-level Fibonacci colours (Plan 0105 phase 5, ADR-0100 rule 2): a fixed
 * `ratio→colour` map — semantically graded shallow→deep (warm shallow pullbacks,
 * green/teal at the watched 0.5/0.618 pair, cool deep), extensions in the
 * violet→magenta family. Static per-element, never a `chartStyle` override;
 * mid-saturation hues stay legible on both themes. */
export const FIB_LEVEL_COLORS: Record<string, string> = {
  '0.236': '#ef5350',
  '0.382': '#f59e0b',
  '0.5': '#16a34a',
  '0.618': '#0d9488',
  '0.786': '#3b82f6',
  '1.272': '#a855f7',
  '1.618': '#7c3aed',
  '2.0': '#c026d3',
  '2.618': '#e11d48',
}

/** The 0/1 swing-anchor boundary lines — neutral slate, distinct from every
 * level hue so the anchors read as the grid's frame, not another level. */
export const FIB_ANCHOR_COLOR = '#94a3b8'

/** The drawn colour for a fib level, falling back to the legend violet for a
 * ratio outside the canonical set. */
export function fibLevelColor(ratio: string): string {
  return FIB_LEVEL_COLORS[ratio] ?? FIB_LINE_COLOR
}

/** The single source of truth for supported overlays. `Partial` because the
 * `OverlayKind` union also carries MVP-unsupported kinds (rsi/macd/bbands) that
 * deliberately have no entry yet. */
export const OVERLAY_REGISTRY: Partial<Record<OverlayKind, OverlayDefinition>> = {
  ema: {
    color: '#2563eb',
    colorToken: '--overlay-ema',
    compute: (bars, spec) => (spec.period != null ? computeEma(bars, spec.period) : []),
  },
  sma: {
    color: '#f97316',
    colorToken: '--overlay-sma',
    compute: (bars, spec) => (spec.period != null ? computeSma(bars, spec.period) : []),
  },
  // Supertrend (Plan 0049 phase 9): the registry entry makes it a supported
  // overlay (one toggleable legend row, no "unsupported" warning) and colours its
  // legend swatch from the bullish (support / uptrend) token. The chart draws the
  // flip-coloured line as TWO masked series (see CandlestickChart); this generic
  // `compute` returns the single active-band line as a correct fallback for any
  // generic caller, never the chart's actual draw path.
  supertrend: {
    color: DEFAULT_MARKER_COLORS.bullish,
    colorToken: '--marker-bullish',
    compute: (bars, spec) =>
      supertrendActiveBand(
        computeSupertrend(
          bars,
          spec.period ?? SUPERTREND_DEFAULT_PERIOD,
          spec.multiplier ?? SUPERTREND_DEFAULT_MULTIPLIER,
        ),
      ),
  },
  // Ichimoku (Plan 0073 phase 4): the registry entry makes it a supported overlay
  // (one toggleable legend row, no "unsupported" warning) and colours its legend
  // swatch from the Senkou-A / cloud-bull token. The chart draws its five lines +
  // filled cloud as a dedicated PRIMITIVE (see `useIchimokuSeries` / `lib/ichimoku`),
  // never the generic single-line path — this `compute` returns `[]` and is never
  // the actual draw path (the generic overlay hook skips `ichimoku`).
  ichimoku: {
    color: '#16a34a',
    colorToken: '--ichimoku-span-a',
    compute: () => [],
  },
  // Bollinger Bands (Plan 0082 phase 2, ADR-0077): the registry entry makes it a
  // supported overlay (one toggleable legend row, no "unsupported" warning) and
  // colours its legend swatch. The chart draws its three bands (upper/middle/lower)
  // via a dedicated `useBbandsSeries` hook — this generic `compute` returns `[]`
  // and is never the actual draw path (the generic overlay hook skips `bbands`),
  // exactly like `supertrend`/`ichimoku`.
  bbands: {
    color: BBANDS_LINE_COLOR,
    compute: () => [],
  },
  // Momentum oscillators (Plan 0091 phase 6): each is a supported overlay (one
  // toggleable legend row, no "unsupported" warning) drawn in its OWN v5 sub-pane
  // by `useOscillatorPanes` (from `lib/oscillators`), never the generic single-line
  // price-pane path — so like bbands/ichimoku this `compute` returns `[]`. The
  // colour is the legend swatch (a static per-oscillator hue, not user-styleable).
  stochastic: { color: '#0891b2', compute: () => [] },
  stoch_rsi: { color: '#0ea5e9', compute: () => [] },
  cci: { color: '#a855f7', compute: () => [] },
  williams_r: { color: '#e11d48', compute: () => [] },
  roc: { color: '#ca8a04', compute: () => [] },
  // Money-flow (Plan 0091 phase 7): same sub-pane draw path via `useOscillatorPanes`.
  mfi: { color: '#0d9488', compute: () => [] },
  cmf: { color: '#7c3aed', compute: () => [] },
  ad_line: { color: '#c2410c', compute: () => [] },
  // RSI + MACD-histogram (Plan 0091 phase 9): promoted from unrendered reserved
  // kinds to real oscillator sub-panes so price↔RSI / price↔MACD divergence
  // segments have a pane to draw on. Same sub-pane draw path via `useOscillatorPanes`
  // (`compute` returns `[]`); `macd` draws its histogram line.
  rsi: { color: '#4f46e5', compute: () => [] },
  macd: { color: '#0284c7', compute: () => [] },
  // Price-structure geometry (Plan 0092 phase 5): each is a supported overlay (one
  // toggleable legend row, no "unsupported" warning). `fibonacci`/`pivot_points`
  // draw as horizontal price lines via `useStructureLevels`; `anchored_vwap` draws
  // a line series via `useAnchoredVwapSeries` — none use the generic single-line
  // path, so like bbands/ichimoku this `compute` returns `[]`. The colour is the
  // legend swatch (static per-kind, not user-styleable — ADR-0062).
  fibonacci: { color: FIB_LINE_COLOR, compute: () => [] },
  pivot_points: { color: PIVOT_LINE_COLOR, compute: () => [] },
  anchored_vwap: { color: ANCHORED_VWAP_COLOR, compute: () => [] },
}

/** The Plan-0092 price-structure geometry kinds — drawn on the price pane by their
 * own dedicated hooks (`useStructureLevels` for fib/pivot lines, `useAnchoredVwapSeries`
 * for anchored VWAP), so the generic overlay/line reconcile skips them. */
export const STRUCTURE_KINDS: readonly OverlayKind[] = [
  'fibonacci',
  'pivot_points',
  'anchored_vwap',
]

/** Whether an overlay kind is a Plan-0092 structure overlay (dedicated draw path). */
export function isStructureOverlay(kind: OverlayKind): boolean {
  return (STRUCTURE_KINDS as readonly string[]).includes(kind)
}

/** The Plan-0091 oscillator + money-flow kinds — drawn in their own v5 sub-panes
 * (not on the price pane), so the generic overlay/line reconcile skips them and
 * `useOscillatorPanes` owns their draw. */
export const OSCILLATOR_KINDS: readonly OverlayKind[] = [
  'stochastic',
  'stoch_rsi',
  'cci',
  'williams_r',
  'roc',
  'mfi',
  'cmf',
  'ad_line',
  // Plan 0091 phase 9: RSI + MACD-histogram draw in their own sub-panes too.
  'rsi',
  'macd',
]

/** Whether an overlay kind draws in its own oscillator sub-pane (Plan 0091). */
export function isOscillatorOverlay(kind: OverlayKind): boolean {
  return (OSCILLATOR_KINDS as readonly string[]).includes(kind)
}

const FALLBACK_COLOR = '#888888'

/** Whether an overlay kind is renderable (has a registry entry). Read at call
 * time so a registry mutation (e.g. in a test) is reflected immediately. */
export function isSupportedOverlay(kind: OverlayKind): boolean {
  return kind in OVERLAY_REGISTRY
}

/** Stable layers-legend id for an indicator overlay (Plan 0047 phase 9). Mirrors
 * the chart's reconcile key so toggling a row maps to exactly one drawn series. */
export function overlayLayerId(spec: OverlaySpec): string {
  return `overlay:${spec.kind}:${spec.period ?? 'na'}`
}

/** Line color for an overlay, falling back to neutral grey for an
 * unregistered kind (which the reconcile loop never actually draws). */
export function overlayColorFor(spec: OverlaySpec): string {
  return OVERLAY_REGISTRY[spec.kind]?.color ?? FALLBACK_COLOR
}

/** CSS custom property a registered overlay resolves its color from, or `null`
 * for a kind with no token (then `overlayColorFor` is used directly). The chart
 * reads this token off the themed DOM so the line recolors with the theme. */
export function overlayColorTokenFor(spec: OverlaySpec): string | null {
  return OVERLAY_REGISTRY[spec.kind]?.colorToken ?? null
}

/** The chart-style element an overlay line resolves its user-overridable colour +
 * width from (Plan 0068 phase 2, ADR-0062). Only `ema`/`sma` have a styleable
 * entry today; any other kind (supertrend, future kinds) returns `null` and the
 * chart keeps the registry's static colour + the default overlay width. */
export function overlayStyleElement(spec: OverlaySpec): ChartLineElement | null {
  if (spec.kind === 'ema') return 'ema'
  if (spec.kind === 'sma') return 'sma'
  return null
}

/** Compute the overlay's line data, or `[]` for an unregistered kind or a
 * missing period. The underlying indicator returns `[]` for too-short input. */
export function computeOverlayData(bars: Bar[], spec: OverlaySpec): LineData[] {
  const definition = OVERLAY_REGISTRY[spec.kind]
  if (definition === undefined) return []
  return definition.compute(bars, spec)
}

/** One Supertrend reading: the active trailing-stop band value and the trend
 * direction (`+1` uptrend → the line sits below price on the lower band; `-1`
 * downtrend → above price on the upper band). Mirrors the pydantic
 * `SupertrendValue`. */
export interface SupertrendPoint {
  time: UTCTimestamp
  value: number
  direction: 1 | -1
}

function toUtcSeconds(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp
}

/**
 * Client-side Supertrend, a faithful mirror of
 * `src/market_analyser/analysis/indicators.py::supertrend` (Wilder ATR seeded at
 * index `period`, recursive final bands, downtrend-seeded direction that flips
 * when the close pierces the active band). Display-only — outside the
 * determinism-critical backtest path (like `ema`/`sma`, also client-computed);
 * `overlays.test.ts` pins this against the Python reference within 1e-6.
 *
 * Returns one point per bar from index `period` onward (earlier bars are
 * undefined), or `[]` when there are too few bars / invalid params.
 */
export function computeSupertrend(
  bars: Bar[],
  period = SUPERTREND_DEFAULT_PERIOD,
  multiplier = SUPERTREND_DEFAULT_MULTIPLIER,
): SupertrendPoint[] {
  const n = bars.length
  if (period < 1 || multiplier <= 0 || n <= period) return []

  // True range (undefined at i = 0 — no previous close).
  const tr: Array<number | null> = new Array(n).fill(null)
  for (let i = 1; i < n; i++) {
    const { high, low } = bars[i]
    const prevClose = bars[i - 1].close
    tr[i] = Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose))
  }

  // Wilder-smoothed ATR, seeded by the SMA of tr[1..period] at index `period`.
  let seedSum = 0
  for (let i = 1; i <= period; i++) {
    const v = tr[i]
    if (v === null) return []
    seedSum += v
  }
  const atr: Array<number | null> = new Array(n).fill(null)
  let prevAtr = seedSum / period
  atr[period] = prevAtr
  for (let i = period + 1; i < n; i++) {
    prevAtr = (prevAtr * (period - 1) + (tr[i] as number)) / period
    atr[i] = prevAtr
  }

  // Basic bands hl2 ± multiplier * ATR, then the recursive final bands.
  const finalUpper: Array<number | null> = new Array(n).fill(null)
  const finalLower: Array<number | null> = new Array(n).fill(null)
  const hl2 = (i: number): number => (bars[i].high + bars[i].low) / 2
  finalUpper[period] = hl2(period) + multiplier * (atr[period] as number)
  finalLower[period] = hl2(period) - multiplier * (atr[period] as number)
  for (let i = period + 1; i < n; i++) {
    const a = atr[i] as number
    const bu = hl2(i) + multiplier * a
    const bl = hl2(i) - multiplier * a
    const prevFu = finalUpper[i - 1] as number
    const prevFl = finalLower[i - 1] as number
    const prevClose = bars[i - 1].close
    finalUpper[i] = bu < prevFu || prevClose > prevFu ? bu : prevFu
    finalLower[i] = bl > prevFl || prevClose < prevFl ? bl : prevFl
  }

  // Direction seeded "down" at index `period` (active band is the upper band),
  // flipping when the close pierces the active band.
  const out: SupertrendPoint[] = []
  let direction: 1 | -1 = -1
  out.push({
    time: toUtcSeconds(bars[period].event_ts),
    value: finalUpper[period] as number,
    direction,
  })
  for (let i = period + 1; i < n; i++) {
    const fu = finalUpper[i] as number
    const fl = finalLower[i] as number
    const close = bars[i].close
    if (direction === -1 && close > fu) direction = 1
    else if (direction === 1 && close < fl) direction = -1
    out.push({ time: toUtcSeconds(bars[i].event_ts), value: direction === 1 ? fl : fu, direction })
  }
  return out
}

/** Split a Supertrend series into two masked line series so the chart can draw
 * the uptrend portion (lower band) and downtrend portion (upper band) in
 * different theme colours: each series carries the value where its direction is
 * active and a whitespace (gap) point otherwise — the line flips colour at each
 * trend change. */
export function supertrendBands(points: SupertrendPoint[]): {
  up: Array<LineData | WhitespaceData>
  down: Array<LineData | WhitespaceData>
} {
  const up: Array<LineData | WhitespaceData> = []
  const down: Array<LineData | WhitespaceData> = []
  for (const p of points) {
    up.push(p.direction === 1 ? { time: p.time, value: p.value } : { time: p.time })
    down.push(p.direction === -1 ? { time: p.time, value: p.value } : { time: p.time })
  }
  return { up, down }
}

/** The single active-band line (one colour) — the generic `computeOverlayData`
 * fallback. The chart's real draw uses `supertrendBands` for flip colouring. */
function supertrendActiveBand(points: SupertrendPoint[]): LineData[] {
  return points.map((p) => ({ time: p.time, value: p.value }))
}
