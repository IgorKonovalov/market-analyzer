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
 * indicator kinds with their own dedicated draw paths. `rsi`/`macd` remain
 * reserved `OverlayKind` values with no registry entry yet, so the chart
 * logs-and-skips them (see `isSupportedOverlay`).
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
