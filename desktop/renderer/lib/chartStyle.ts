/**
 * User chart-style overrides (Plan 0068 phase 1, ADR-0062).
 *
 * A single typed store, shaped like `theme.ts`, that lets the user restyle the
 * candlestick chart: per-theme **colour** and **line width** for each built-in
 * series + agent overlay line, plus a global **candle series-type** (candles /
 * OHLC bars / line / area). Overrides persist in `localStorage['ma.chartStyle']`
 * (extending ADR-0039's presentation-pref convention — no sidecar, no config.json)
 * and resolve through one `resolveChartStyle(container, theme)` that reads the
 * theme's *default* palette off the DOM tokens (styles.css stays the default
 * source of truth — lightweight-charts can't resolve `var()`), layers the user's
 * per-theme overrides on top, fills width defaults, and returns a fully-resolved
 * concrete `ResolvedChartStyle`. `CandlestickChart` consumes that object in place
 * of the old `readChartColors` / `overlaySeriesColor` reads and the hard-coded
 * `lineWidth` literals, and re-applies changes in place via `applyOptions`.
 *
 * Overrides are keyed by element **type**, not instance (all EMA lines share one
 * colour) — the agent's overlay instances are ephemeral (ADR-0062 alt C rejected).
 * Colour + width are per theme (a green legible on dark isn't legible on light);
 * candle-type is a single theme-independent render mode.
 *
 * Every `localStorage` access is wrapped in try/catch: in a sandboxed or privacy
 * context reads/writes can throw, and the contract is to degrade to defaults
 * (session-only mutations) rather than crash — exactly as `theme.ts` does.
 */
import type { EffectiveTheme } from './theme'

/** The styleable elements — the fixed built-in roster + the agent overlay line
 * types. Each maps to a theme token (its default colour) below. Supertrend,
 * price-lines, and the pattern-span band derive from the bull/bear/neutral marker
 * colours, so overriding those marker entries recolours them for free (ADR-0062). */
export type ChartStyleElement =
  | 'candleUp'
  | 'candleDown'
  | 'volume'
  | 'volumeMa'
  | 'vwap'
  | 'obv'
  | 'ema'
  | 'sma'
  | 'markerBullish'
  | 'markerBearish'
  | 'markerNeutral'

/** The subset of elements drawn as lines, which additionally take a width. */
export type ChartLineElement = 'volumeMa' | 'vwap' | 'obv' | 'ema' | 'sma'

/** Candle series render mode. `candles` is today's behaviour (the default). */
export type CandleSeriesType = 'candles' | 'bars' | 'line' | 'area'

/** A single element's overrides. `lineWidth` is meaningful only for line
 * elements; the resolver ignores it for the others. */
export interface ElementOverride {
  color?: string
  lineWidth?: number
}

type ThemeOverrides = Partial<Record<ChartStyleElement, ElementOverride>>

/** The persisted model: per-theme element overrides + a global candle-type. */
export interface ChartStyleOverrides {
  light: ThemeOverrides
  dark: ThemeOverrides
  /** Global, theme-independent (a render mode, not a colour). Absent = `candles`. */
  candleType?: CandleSeriesType
}

/** The concrete object the chart consumes: defaults ⊕ overrides, resolved for one
 * theme. `colors` covers the styleable elements; `chrome` carries the
 * non-overridable chart-chrome colours (axis text, grid border, clicked-bar
 * marker) so the chart still reads every drawn colour from one place. */
export interface ResolvedChartStyle {
  colors: Record<ChartStyleElement, string>
  widths: Record<ChartLineElement, number>
  candleType: CandleSeriesType
  chrome: { text: string; border: string; markerClicked: string }
}

/** The styleable elements, in display order (drives the Settings controls). */
export const CHART_STYLE_ELEMENTS: readonly ChartStyleElement[] = [
  'candleUp',
  'candleDown',
  'volume',
  'volumeMa',
  'vwap',
  'obv',
  'ema',
  'sma',
  'markerBullish',
  'markerBearish',
  'markerNeutral',
]

/** The line elements that take a width, in display order. */
export const CHART_LINE_ELEMENTS: readonly ChartLineElement[] = [
  'volumeMa',
  'vwap',
  'obv',
  'ema',
  'sma',
]

/** Whether an element is drawn as a width-bearing line. */
export function isLineElement(element: ChartStyleElement): element is ChartLineElement {
  return (CHART_LINE_ELEMENTS as readonly string[]).includes(element)
}

/** The CSS custom property each element resolves its default colour from. Mirrors
 * the tokens in `styles.css` (and the old `readChartColors` / overlay registry). */
const ELEMENT_TOKENS: Record<ChartStyleElement, string> = {
  candleUp: '--chart-up',
  candleDown: '--chart-down',
  volume: '--chart-volume',
  volumeMa: '--overlay-volume-ma',
  vwap: '--overlay-vwap',
  obv: '--overlay-obv',
  ema: '--overlay-ema',
  sma: '--overlay-sma',
  markerBullish: '--marker-bullish',
  markerBearish: '--marker-bearish',
  markerNeutral: '--marker-neutral',
}

/** Light-theme fallback colours, used when a token is unset — e.g. in jsdom unit
 * tests where styles.css isn't loaded. At runtime the DOM tokens win and follow
 * the chosen theme. Kept byte-equal to the old `CHART_COLOR_FALLBACK` so a
 * no-override resolution renders exactly today's colours. */
const ELEMENT_FALLBACK: Record<ChartStyleElement, string> = {
  candleUp: '#15803d',
  candleDown: '#b42318',
  volume: '#cbd5e1',
  volumeMa: '#64748b',
  vwap: '#9333ea',
  obv: '#0891b2',
  ema: '#2563eb',
  sma: '#f97316',
  markerBullish: '#10b981',
  markerBearish: '#f43f5e',
  markerNeutral: '#64748b',
}

const CHROME_FALLBACK = { text: '#1a1a1a', border: '#e5e5e5', markerClicked: '#2563eb' } as const

/** Default line widths — the literals the chart used before this store existed
 * (MA/OBV thin, VWAP/EMA/SMA slightly heavier). */
const DEFAULT_WIDTHS: Record<ChartLineElement, number> = {
  volumeMa: 1,
  vwap: 2,
  obv: 1,
  ema: 2,
  sma: 2,
}

/** Inclusive line-width clamp (Plan 0068 open question, resolved at 1–4). */
export const MIN_LINE_WIDTH = 1
export const MAX_LINE_WIDTH = 4

function clampWidth(width: number): number {
  if (!Number.isFinite(width)) return DEFAULT_WIDTHS.ema
  const rounded = Math.round(width)
  return Math.min(MAX_LINE_WIDTH, Math.max(MIN_LINE_WIDTH, rounded))
}

const STORAGE_KEY = 'ma.chartStyle'
const CANDLE_TYPES: readonly CandleSeriesType[] = ['candles', 'bars', 'line', 'area']

type Listener = () => void
const listeners = new Set<Listener>()

/** In-memory source of truth, hydrated once from storage. Mutators update this and
 * write through; a blocked write leaves the in-memory copy intact (session-only). */
let overrides: ChartStyleOverrides = load()

function emptyOverrides(): ChartStyleOverrides {
  return { light: {}, dark: {} }
}

/** Coerce an unknown parsed value into a well-formed `ChartStyleOverrides`,
 * dropping anything malformed. Guarantees the store never holds — nor the chart
 * ever reads — a bad shape, so blocked/corrupt storage degrades to defaults. */
function sanitize(raw: unknown): ChartStyleOverrides {
  const result = emptyOverrides()
  if (typeof raw !== 'object' || raw === null) return result
  const obj = raw as Record<string, unknown>
  for (const theme of ['light', 'dark'] as const) {
    const themeRaw = obj[theme]
    if (typeof themeRaw !== 'object' || themeRaw === null) continue
    const themeObj = themeRaw as Record<string, unknown>
    for (const element of CHART_STYLE_ELEMENTS) {
      const patch = sanitizeOverride(themeObj[element])
      if (patch !== null) result[theme][element] = patch
    }
  }
  if (CANDLE_TYPES.includes(obj.candleType as CandleSeriesType)) {
    result.candleType = obj.candleType as CandleSeriesType
  }
  return result
}

function sanitizeOverride(raw: unknown): ElementOverride | null {
  if (typeof raw !== 'object' || raw === null) return null
  const obj = raw as Record<string, unknown>
  const patch: ElementOverride = {}
  if (typeof obj.color === 'string' && obj.color.length > 0) patch.color = obj.color
  if (typeof obj.lineWidth === 'number' && Number.isFinite(obj.lineWidth)) {
    patch.lineWidth = clampWidth(obj.lineWidth)
  }
  return patch.color !== undefined || patch.lineWidth !== undefined ? patch : null
}

function load(): ChartStyleOverrides {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === null) return emptyOverrides()
    return sanitize(JSON.parse(stored) as unknown)
  } catch {
    /* localStorage blocked or JSON malformed → defaults, session-only */
    return emptyOverrides()
  }
}

function persist(): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(overrides))
  } catch {
    /* localStorage blocked → in-memory copy is the session-only source of truth */
  }
}

function notify(): void {
  for (const listener of listeners) listener()
}

/** Read the current override model (a snapshot; treat as read-only). */
export function getChartStyleOverrides(): ChartStyleOverrides {
  return overrides
}

/** The currently-selected candle series-type (`candles` when unset). */
export function getCandleType(): CandleSeriesType {
  return overrides.candleType ?? 'candles'
}

/**
 * Merge a colour/width patch into one element's override for one theme, persist,
 * and notify subscribers. A width is clamped to [MIN,MAX]. Passing `color:
 * undefined` / `lineWidth: undefined` in the patch does not clear an existing
 * value — use `resetChartStyle` for a full clear.
 */
export function setElementOverride(
  theme: EffectiveTheme,
  element: ChartStyleElement,
  patch: ElementOverride,
): void {
  const current = overrides[theme][element] ?? {}
  const next: ElementOverride = { ...current }
  if (patch.color !== undefined) next.color = patch.color
  if (patch.lineWidth !== undefined) next.lineWidth = clampWidth(patch.lineWidth)
  overrides = { ...overrides, [theme]: { ...overrides[theme], [element]: next } }
  persist()
  notify()
}

/** Set the global candle series-type, persist, and notify subscribers. */
export function setCandleType(type: CandleSeriesType): void {
  overrides = { ...overrides, candleType: type }
  persist()
  notify()
}

/** Clear every override (colours, widths, candle-type) back to defaults. */
export function resetChartStyle(): void {
  overrides = emptyOverrides()
  persist()
  notify()
}

/**
 * Subscribe to any style mutation (`setElementOverride`, `setCandleType`,
 * `resetChartStyle`). Returns an unsubscribe function; call it on unmount. The
 * callback takes no argument — the subscriber re-reads via `resolveChartStyle` /
 * `getChartStyleOverrides` (the current DOM theme drives which base tokens apply).
 */
export function subscribeChartStyle(callback: Listener): () => void {
  listeners.add(callback)
  return () => {
    listeners.delete(callback)
  }
}

/**
 * Resolve the concrete style the chart draws with, for one theme: read each
 * element's default colour from its DOM token (falling back to the light default
 * when unset), layer the theme's user override on top, fill width defaults, and
 * carry the global candle-type. `theme` selects *which* per-theme override set
 * applies; the base colours come from `container`'s computed tokens, which the
 * caller keeps in sync with the applied DOM theme.
 */
export function resolveChartStyle(
  container: HTMLElement,
  theme: EffectiveTheme,
): ResolvedChartStyle {
  const computed = getComputedStyle(container)
  const token = (name: string, fallback: string): string =>
    computed.getPropertyValue(name).trim() || fallback
  const themeOverrides = overrides[theme]

  const colors = {} as Record<ChartStyleElement, string>
  for (const element of CHART_STYLE_ELEMENTS) {
    const base = token(ELEMENT_TOKENS[element], ELEMENT_FALLBACK[element])
    colors[element] = themeOverrides[element]?.color ?? base
  }

  const widths = {} as Record<ChartLineElement, number>
  for (const element of CHART_LINE_ELEMENTS) {
    const override = themeOverrides[element]?.lineWidth
    widths[element] = override !== undefined ? clampWidth(override) : DEFAULT_WIDTHS[element]
  }

  return {
    colors,
    widths,
    candleType: getCandleType(),
    chrome: {
      text: token('--color-fg', CHROME_FALLBACK.text),
      border: token('--color-border', CHROME_FALLBACK.border),
      markerClicked: token('--marker-clicked', CHROME_FALLBACK.markerClicked),
    },
  }
}
