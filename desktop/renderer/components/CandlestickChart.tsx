/**
 * Lightweight-charts wrapper. Three effects, three responsibilities:
 *   1. Create the chart once on mount; dispose on unmount.
 *   2. Push data when `bars` change; never recreate the chart for new data;
 *      reconcile overlay series (Plan 0007 phase 4.5) when `overlays` or
 *      `bars` change: add new line series, remove gone ones, recompute
 *      data for the kept ones.
 *   3. Push markers when `annotations` (or the clicked-bar marker) change;
 *      layer onto the candlestick.
 *
 * The pointer-gesture state machine (agent-mode range-select + bar-click) lives
 * in `useChartGestures` (Plan 0029 phase 1); the component owns chart lifecycle
 * and declarative series reconciliation and hands the hook its chart/series refs.
 *
 * Disposing on unmount is non-negotiable — without it every navigation leaks
 * a Canvas/WebGL context. See ui-builder/references/best-practices.md.
 *
 * The renderer exposes `window.__test_chart_render__` reflecting what's
 * actually drawn on the chart (one entry per series, including the
 * candlestick). Playwright `live-chart.spec.ts` and the renderer-side unit
 * spec assert against that — NOT the reducer's overlay list — so a render
 * regression that loses a series cannot pass.
 */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { ColorType, TickMarkType, createChart } from 'lightweight-charts'
import type {
  IChartApi,
  ISeriesApi,
  LineData,
  LineWidth,
  Logical,
  MouseEventParams,
  SeriesMarker,
  Time,
  UTCTimestamp,
} from 'lightweight-charts'

import type { IPriceLine } from 'lightweight-charts'

import { ApiError, api, toLightweightBar } from '../api/client'
import { t } from '../lib/i18n'
import { useChartGestures } from '../hooks/useChartGestures'
import { useChartPatternRecompute } from '../hooks/useChartPatternRecompute'
import { useLazyHistoryTrigger } from '../hooks/useLazyHistoryTrigger'
import {
  DEFAULT_MARKER_COLORS,
  annotationsToMarkers,
  type ChartMarker,
  markerLayerId,
  markerLayerLabel,
} from '../lib/markers'
import {
  type OverlayReading,
  type TooltipContent,
  overlayLabel,
  tooltipAtTime,
  trendlineTooltipText,
} from '../lib/tooltip'
import { ChartTooltip } from './ChartTooltip'
import { LayersPanel, type ChartLayer } from './LayersPanel'
import {
  computeOverlayData,
  computeSupertrend,
  isSupportedOverlay,
  overlayColorFor,
  overlayLayerId,
  overlayStyleElement,
  supertrendBands,
} from '../lib/overlays'
import { PatternSpanPrimitive, SPAN_LAYER_ID, SPAN_LAYER_LABEL, markersToSpans } from '../lib/spans'
import {
  TrendlinePrimitive,
  dedupeTrendlines,
  patternDisplayName,
  patternStateKey,
  readTrendlineColors,
  trendlineColor,
  trendlineGroupLayerId,
  trendlineStateLabel,
} from '../lib/trendlines'
import { useTrendlines } from '../hooks/useTrendlines'
import {
  getStoredTheme,
  resolveEffective,
  subscribeEffective,
  type EffectiveTheme,
} from '../lib/theme'
import {
  getCandleType,
  resolveChartStyle,
  subscribeChartStyle,
  type CandleSeriesType,
  type ResolvedChartStyle,
} from '../lib/chartStyle'
import {
  VOLUME_MA_PERIOD,
  VWAP_PERIOD,
  computeObv,
  computeVolumeBars,
  computeVolumeMa,
  computeVwap,
} from '../lib/volume'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec, TrendlineSpec } from '../types/events'
import type { QuoteResponse } from '../types/sidecar/quote-response'
import { timeframeDurationMs } from '../lib/timeframes'
import styles from './CandlestickChart.module.css'

// Default width for an agent overlay line whose kind has no styleable width
// (i.e. not ema/sma) — matches the literal the chart used before Plan 0068.
const DEFAULT_OVERLAY_LINE_WIDTH = 2 as LineWidth

interface ChartColors {
  text: string
  border: string
  candleUp: string
  candleDown: string
  volume: string
  volumeMa: string
  vwap: string
  obv: string
  markerClicked: string
  markerBullish: string
  markerBearish: string
  markerNeutral: string
}

/** Flatten a resolved chart style into the flat colour view several effects read
 * (the styleable colours ⊕ the non-overridable chrome). Every drawn colour now
 * resolves through the chart-style store (Plan 0068 phase 2, ADR-0062): styles.css
 * theme tokens are the defaults, the user's per-theme overrides layer on top, and
 * lightweight-charts is handed fully-resolved strings (it can't resolve `var()`). */
function chartColorsFrom(style: ResolvedChartStyle): ChartColors {
  return { ...style.colors, ...style.chrome }
}

/** An agent overlay line's resolved colour: ema/sma read their styleable entry
 * (honouring the user's override); any other kind keeps the registry colour. */
function overlayStyleColor(spec: OverlaySpec, style: ResolvedChartStyle): string {
  const element = overlayStyleElement(spec)
  return element ? style.colors[element] : overlayColorFor(spec)
}

/** An agent overlay line's resolved width: ema/sma read their styleable width;
 * any other kind keeps the default overlay width. */
function overlayStyleWidth(spec: OverlaySpec, style: ResolvedChartStyle): LineWidth {
  const element = overlayStyleElement(spec)
  return (element ? style.widths[element] : DEFAULT_OVERLAY_LINE_WIDTH) as LineWidth
}

// Stable no-op for the lazy-history trigger when no `onReachLeftEdge` is wired
// (keeps the trigger hook's callback ref from churning on every render).
const NOOP = (): void => {}

// Always-on volume series (Plan 0027 phase 3), each derived client-side from
// `bars`. The histogram + its MA sit on their own bottom-band price scale; VWAP
// rides the main price scale; OBV gets its own band. lightweight-charts 4.2.x has
// no panes API, so "own pane" is an overlay price scale with `scaleMargins` (the
// plan's documented v4 mechanism / OBV fallback).
const PRICE_SCALE_ID = 'right' // the default price (candlestick) scale
const VOLUME_SCALE_ID = 'volume'
const OBV_SCALE_ID = 'obv'
// Candles occupy the upper band; volume hugs the bottom; OBV gets a strip above it.
const PRICE_SCALE_MARGINS = { top: 0.05, bottom: 0.4 }
const VOLUME_SCALE_MARGINS = { top: 0.82, bottom: 0 }
const OBV_SCALE_MARGINS = { top: 0.62, bottom: 0.22 }

// The main price series across the four render modes (Plan 0068 phase 4). A
// candle-type change rebuilds the chart (the series type is fixed at creation),
// so the whole creation effect re-runs with a fresh series of this type.
type MainSeries = ISeriesApi<'Candlestick' | 'Bar' | 'Line' | 'Area'>

/** The `__test_chart_render__` kind reported for the main series of each type. */
function mainSeriesKind(type: CandleSeriesType): string {
  switch (type) {
    case 'bars':
      return 'bar'
    case 'line':
      return 'line'
    case 'area':
      return 'area'
    case 'candles':
    default:
      return 'candlestick'
  }
}

/** Create the main price series for the chosen render type. Colours are applied
 * separately by `applyMainColors` so creation and the restyle effect share one
 * colour source. Line/area ride the main price scale (as candles do by default). */
function createMainSeries(chart: IChartApi, type: CandleSeriesType): MainSeries {
  const common = { priceLineVisible: false, lastValueVisible: false }
  switch (type) {
    case 'bars':
      return chart.addBarSeries({})
    case 'line':
      return chart.addLineSeries({ priceScaleId: PRICE_SCALE_ID, lineWidth: 2, ...common })
    case 'area':
      return chart.addAreaSeries({ priceScaleId: PRICE_SCALE_ID, lineWidth: 2, ...common })
    case 'candles':
    default:
      return chart.addCandlestickSeries({})
  }
}

/** Apply the resolved colours to the main series for its render type. Candles/bars
 * take up/down (+ wick/border) colours; line/area have no up/down concept, so the
 * single line colour maps from `candleUp` (ADR-0062: the up/down/wick controls are
 * inert then, and the Settings UI disables them). */
function applyMainColors(series: MainSeries, type: CandleSeriesType, colors: ChartColors): void {
  if (type === 'bars') {
    ;(series as ISeriesApi<'Bar'>).applyOptions({
      upColor: colors.candleUp,
      downColor: colors.candleDown,
    })
  } else if (type === 'line') {
    ;(series as ISeriesApi<'Line'>).applyOptions({ color: colors.candleUp })
  } else if (type === 'area') {
    ;(series as ISeriesApi<'Area'>).applyOptions({
      lineColor: colors.candleUp,
      topColor: colors.candleUp,
      bottomColor: 'transparent',
    })
  } else {
    ;(series as ISeriesApi<'Candlestick'>).applyOptions({
      upColor: colors.candleUp,
      downColor: colors.candleDown,
      wickUpColor: colors.candleUp,
      wickDownColor: colors.candleDown,
      borderUpColor: colors.candleUp,
      borderDownColor: colors.candleDown,
    })
  }
}

/** Push the bars onto the main series in the shape its render type expects:
 * OHLC for candles/bars, a single `value` (close) for line/area. */
function setMainData(series: MainSeries, type: CandleSeriesType, bars: Bar[]): void {
  if (type === 'line' || type === 'area') {
    ;(series as ISeriesApi<'Line'>).setData(
      bars.map((b) => {
        const d = toLightweightBar(b)
        return { time: d.time, value: d.close }
      }),
    )
  } else {
    ;(series as ISeriesApi<'Candlestick'>).setData(bars.map(toLightweightBar))
  }
}

declare global {
  interface Window {
    __test_chart_render__?: {
      seriesCount: number
      seriesKinds: ReadonlyArray<{ kind: string; period?: number | null }>
      /** Candlestick bars currently set on the series (Plan 0030: the lazy-load
       * e2e asserts this grows after a left-edge prepend). */
      barCount: number
    }
  }
}

// Stable empty list for the trendlines default — a fresh `[]` per render would
// re-run the useTrendlines effect every time.
const NO_TRENDLINES: ReadonlyArray<TrendlineSpec> = []

interface Props {
  bars: Bar[]
  annotations?: ChartMarker[]
  overlays?: ReadonlyArray<OverlaySpec>
  /** Plan 0052 phase 4 (ADR-0049): sloped trendlines (necklines, triangle/wedge
   * bounds) from `chart.show`/`chart.update`, drawn by the trendline primitive
   * via `useTrendlines`. Dashed = forming, solid = confirmed. */
  trendlines?: ReadonlyArray<TrendlineSpec>
  ariaLabel?: string
  /** Plan 0014: when true, chart gestures (range-select, bar-click) are
   * forwarded to the agent via `POST /ui_events`. Default false — the
   * `AgentModeToggle` owns this state and threads it down. */
  agentModeEnabled?: boolean
  /** Carried in the gesture payloads so the agent knows which chart fired. */
  symbol?: string
  timeframe?: string
  /** Plan 0049 phase 10: the live quote the parent already polls (`useQuotePoll`).
   * When its `as_of` falls within the latest bar's period, the chart updates the
   * forming bar in place (no refetch, no new bar). */
  quote?: QuoteResponse | null
  /** Plan 0030: fired when the user scrolls near the buffer's left edge so the
   * parent can fetch + prepend older bars. */
  onReachLeftEdge?: () => void
  /** Gate for the left-edge trigger — false while an older fetch is in flight
   * or the start of available history has been reached. */
  historyTriggerEnabled?: boolean
}

/** Transient state of the "Scan patterns" sweep (Plan 0049 phase 8). The markers
 * arrive via SSE; this only tracks the trigger's ack so the button can show
 * progress / a count / "nothing in view" / an error. */
type ScanStatus =
  | { kind: 'idle' }
  | { kind: 'scanning' }
  | { kind: 'done'; count: number }
  | { kind: 'empty' }
  | { kind: 'error'; message: string }

/** Human-readable label for a selected [start, end] window. UTC (matching the
 * bar timestamps); the time is shown only when it isn't midnight, so a daily
 * range reads as plain dates. */
function formatRangeLabel(startIso: string, endIso: string): string {
  const fmt = (iso: string): string => {
    const date = iso.slice(0, 10)
    const time = iso.slice(11, 16)
    return time === '00:00' ? date : `${date} ${time}`
  }
  return `${fmt(startIso)} → ${fmt(endIso)}`
}

/** Axis tick formatter for the monthly (`1mo`) timeframe (Plan 0050 phase 7).
 * Month-spaced bars must read as month/year, never day-of-month or intraday
 * labels (which lightweight-charts' default would emit for some zoom levels,
 * producing repeated "1" day labels). Year boundaries show the year; every other
 * tick shows the abbreviated month. UTC, matching the bar timestamps. */
function monthlyTickMarkFormatter(time: Time, tickMarkType: TickMarkType, locale: string): string {
  const ms = typeof time === 'number' ? time * 1000 : Date.parse(String(time))
  const date = new Date(ms)
  if (tickMarkType === TickMarkType.Year) {
    return String(date.getUTCFullYear())
  }
  return date.toLocaleDateString(locale, { month: 'short', timeZone: 'UTC' })
}

interface OverlayEntry {
  spec: OverlaySpec
  series: ISeriesApi<'Line'>
}

function overlayKey(spec: OverlaySpec): string {
  return `${spec.kind}:${spec.period ?? 'na'}`
}

/** Layers-legend id for a `price_line` overlay (Plan 0047 phase 9). */
function priceLineId(spec: OverlaySpec): string {
  return `pline:${spec.label ?? spec.price ?? 'na'}`
}

/** Display label for a price line in the legend, e.g. `R1 (61335.75)`. */
function priceLineLabel(spec: OverlaySpec): string {
  const name = spec.label ?? 'level'
  return spec.price != null ? `${name} (${spec.price})` : name
}

/** Price-line colour: a support level reads bullish, a resistance level bearish,
 * a roleless level uses the neutral clicked/accent token — so the legend swatch
 * matches the drawn line. */
function priceLineColor(spec: OverlaySpec, colors: ChartColors): string {
  if (spec.role === 'support') return colors.markerBullish
  if (spec.role === 'resistance') return colors.markerBearish
  return colors.markerClicked
}

/** The chart's current visible [from, to] window as ISO strings, or null when
 * the chart has no data / no resolvable numeric range yet. Shared by the
 * pattern-scan triggers (markers + trendlines). */
function visibleRangeIso(chart: IChartApi): { range_start: string; range_end: string } | null {
  const range = chart.timeScale().getVisibleRange()
  const toIso = (t: Time): string | null =>
    typeof t === 'number' ? new Date(t * 1000).toISOString() : null
  const rangeStart = range ? toIso(range.from) : null
  const rangeEnd = range ? toIso(range.to) : null
  if (rangeStart === null || rangeEnd === null) return null
  return { range_start: rangeStart, range_end: rangeEnd }
}

export function CandlestickChart({
  bars,
  annotations,
  overlays,
  trendlines = NO_TRENDLINES,
  ariaLabel,
  agentModeEnabled = false,
  symbol,
  timeframe,
  quote,
  onReachLeftEdge,
  historyTriggerEnabled = false,
}: Props): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  // The main price series — its concrete type (candlestick / bar / line / area)
  // is chosen at creation from `candleType` (Plan 0068 phase 4).
  const seriesRef = useRef<MainSeries | null>(null)
  // First-bar timestamp (ms) of the previous render, to detect left-side growth.
  const prevFirstTsRef = useRef<number | null>(null)
  // The `bars` reference from the previous bars-effect run. The effect also runs
  // on overlay/visibility changes (to reconcile series), but only a genuine DATA
  // change (a new `bars` array: load, symbol/tf/range change, lazy-prepend) may
  // refit the view — toggling a layer must preserve the user's zoom/pan
  // (Plan 0049 phase 11).
  const prevBarsRef = useRef<Bar[] | null>(null)
  const overlaySeriesRef = useRef<Map<string, OverlayEntry>>(new Map())
  // Supertrend overlays (Plan 0049 phase 9) draw as TWO masked line series (the
  // up/lower band in the bullish token, the down/upper band in the bearish token)
  // so the trailing-stop line flips colour at trend changes. Keyed by overlayKey.
  const supertrendSeriesRef = useRef<
    Map<string, { up: ISeriesApi<'Line'>; down: ISeriesApi<'Line'> }>
  >(new Map())
  // Drawn price lines (Plan 0047 phase 9), keyed by `priceLineId`. price_line
  // overlays are horizontal lines on the candlestick series, not line series.
  const priceLinesRef = useRef<Map<string, IPriceLine>>(new Map())
  // Multi-bar pattern span band (Plan 0049 phase 7): one series primitive,
  // attached at mount, fed spans/colors/visibility by the spans effect below.
  const spanPrimitiveRef = useRef<PatternSpanPrimitive | null>(null)
  // Trendline overlay primitive (Plan 0052 phase 4, ADR-0049): attached at mount
  // alongside the span band — NOT inside `useTrendlines` — so its lifecycle is
  // tied to the chart's and it always rides the LIVE series (Plan 0064 follow-up:
  // the hook-attach stranded it on a discarded chart under StrictMode). Fed by
  // `useTrendlines` below.
  const trendlinePrimitiveRef = useRef<TrendlinePrimitive | null>(null)
  // Always-on volume series (Plan 0027 phase 3).
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const volumeMaSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const vwapSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const obvSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  // Bar count currently drawn on the candlestick, updated by the bars effect.
  // Held in a ref (not read from `bars` in syncTestRenderHook) so the hook can be
  // a stable useCallback — otherwise listing it in the mount effect's deps would
  // make a `bars` change recreate the chart.
  const barCountRef = useRef(0)
  // Effective theme (light/dark) drives in-place chart recoloring. A change
  // flows through `applyOptions`, never a remount — the chart-creation effect's
  // deps are `[]`, so the instance persists. (Plan 0033 phase 4.)
  const [effectiveTheme, setEffectiveTheme] = useState<EffectiveTheme>(() =>
    resolveEffective(getStoredTheme()),
  )
  // Latest effective theme in a ref so effects that create series (mount, overlay
  // reconcile) can resolve the right theme's style overrides without listing
  // `effectiveTheme` as a dep (which would remount / re-setData on a theme flip).
  // The style-change/theme-recolor effect re-applies existing series in place.
  const effectiveThemeRef = useRef(effectiveTheme)
  effectiveThemeRef.current = effectiveTheme
  // Bumped on any chart-style store mutation (Plan 0068 phase 2). The colour/width
  // effects key on it so a user override re-resolves and re-applies in place — no
  // remount, mirroring the theme-recolor path.
  const [styleVersion, setStyleVersion] = useState(0)
  // The candle series-type (Plan 0068 phase 4). Unlike colour/width, a change here
  // REBUILDS the chart (the series type is fixed at creation): the creation effect
  // keys on it, and the data / marker / primitive effects + the chart-subscribing
  // hooks re-run so everything re-attaches to the fresh series. Only re-renders
  // when the type actually changes (getCandleType is a stable primitive snapshot),
  // so a colour/width mutation doesn't trigger a rebuild.
  const candleType = useSyncExternalStore(subscribeChartStyle, getCandleType, getCandleType)
  // Ephemeral hover-tooltip state (Plan 0047 phase 8): the crosshair content +
  // its position within the chart area. Null while not hovering a labelled bar
  // or an overlay line. Never persisted, never round-tripped to the sidecar.
  const [tooltip, setTooltip] = useState<{ content: TooltipContent; x: number; y: number } | null>(
    null,
  )
  // Layers-legend state (Plan 0047 phase 9), all ephemeral: `hidden` is the set
  // of layer ids the user toggled off; `layers` is the resolved descriptor list
  // the panel renders. Reset on remount (no persistence) by construction.
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set())
  const [layers, setLayers] = useState<ChartLayer[]>([])
  const toggleLayer = useCallback((id: string): void => {
    setHidden((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])
  // Collapse the redundant forming(dashed)+confirmed(solid) twin of each geometry
  // (Plan 0067 phase 1 / ADR-0061) before anything consumes the specs — the
  // primitive draws these, and the legend counts from them. Memoised so a stable
  // `trendlines` reference doesn't re-run the downstream effects.
  const visibleTrendlines = useMemo(() => dedupeTrendlines(trendlines), [trendlines])
  // Legend visibility is per (pattern type, state) group (Plan 0067 phase 3):
  // drop the specs whose group row is unchecked before drawing. The legend rows
  // themselves are still built from the full deduped set (so hidden groups list
  // and can be re-enabled).
  const shownTrendlines = useMemo(
    () => visibleTrendlines.filter((s) => !hidden.has(trendlineGroupLayerId(patternStateKey(s)))),
    [visibleTrendlines, hidden],
  )
  // Hovered trendline legend group (Plan 0067 phase 3): its `patternStateKey`, or
  // null. Threaded into the primitive so hovering a row emphasises that group's
  // lines and dims the rest. Ephemeral, never persisted.
  const [highlightedTrendlineKey, setHighlightedTrendlineKey] = useState<string | null>(null)
  // "Scan patterns" trigger state (Plan 0049 phase 8). Ephemeral, never persisted.
  const [scanStatus, setScanStatus] = useState<ScanStatus>({ kind: 'idle' })
  // "Scan chart patterns" (trendline) trigger state (Plan 0064 phase 5). Only the
  // MANUAL button surfaces this; the mount/range auto-recompute stays silent so a
  // pan doesn't flicker a status label. Ephemeral, never persisted.
  const [chartScanStatus, setChartScanStatus] = useState<ScanStatus>({ kind: 'idle' })

  // Sweep the chart's CURRENT visible range (not the full buffer) for patterns via
  // POST /scan_patterns; the markers come back over SSE (no second draw path). The
  // bearer is injected by the typed client — never a raw fetch.
  const scanVisibleRange = useCallback(async (): Promise<void> => {
    const chart = chartRef.current
    if (!chart || !symbol || !timeframe) return
    const range = chart.timeScale().getVisibleRange()
    const toIso = (t: Time): string | null =>
      typeof t === 'number' ? new Date(t * 1000).toISOString() : null
    const rangeStart = range ? toIso(range.from) : null
    const rangeEnd = range ? toIso(range.to) : null
    if (rangeStart === null || rangeEnd === null) return
    setScanStatus({ kind: 'scanning' })
    try {
      const ack = await api.scanPatterns({
        symbol,
        timeframe,
        range_start: rangeStart,
        range_end: rangeEnd,
      })
      setScanStatus(
        ack.published && ack.count > 0 ? { kind: 'done', count: ack.count } : { kind: 'empty' },
      )
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Pattern scan failed'
      setScanStatus({ kind: 'error', message })
    }
  }, [symbol, timeframe])

  // Recompute the chart-pattern TRENDLINES for the current visible range via
  // POST /scan_chart_patterns (Plan 0064 phase 5, ADR-0059). The lines come back
  // over SSE as a `chart.trendlines` event — this only fires the trigger. Silent
  // (no status churn): used by the mount + debounced-range auto-recompute so the
  // lines track the bars on screen and survive a reload. Bearer via the typed
  // client — never a raw fetch.
  const recomputeTrendlines = useCallback(async (): Promise<void> => {
    const chart = chartRef.current
    if (!chart || !symbol || !timeframe) return
    const range = visibleRangeIso(chart)
    if (range === null) return
    try {
      await api.scanChartPatterns({ symbol, timeframe, ...range })
    } catch (err) {
      // Auto-recompute is best-effort: a failed refresh just leaves the current
      // lines in place. Log, don't surface (the manual trigger reports errors).
      console.warn('[CandlestickChart] trendline recompute failed', err)
    }
  }, [symbol, timeframe])

  // Manual "Scan chart patterns" trigger (Plan 0064 phase 5): same sweep as the
  // auto-recompute but status-tracked for button feedback. Kept distinct from
  // "Scan patterns" (candlestick markers) — marker glyphs and chart-pattern
  // geometry are conceptually separate layers.
  const scanChartPatternsVisibleRange = useCallback(async (): Promise<void> => {
    const chart = chartRef.current
    if (!chart || !symbol || !timeframe) return
    const range = visibleRangeIso(chart)
    if (range === null) return
    setChartScanStatus({ kind: 'scanning' })
    try {
      const ack = await api.scanChartPatterns({ symbol, timeframe, ...range })
      setChartScanStatus(
        ack.published && ack.count > 0 ? { kind: 'done', count: ack.count } : { kind: 'empty' },
      )
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Chart-pattern scan failed'
      setChartScanStatus({ kind: 'error', message })
    }
  }, [symbol, timeframe])

  // Reflect what's drawn into the test hook. Stable identity (reads only refs),
  // so it can sit in the effect dep arrays without retriggering them.
  const syncTestRenderHook = useCallback((): void => {
    const kinds: Array<{ kind: string; period?: number | null }> = []
    if (seriesRef.current !== null) {
      // Read the type from the store (not a dep) so this stays a stable callback;
      // a candle-type change rebuilds via the creation effect, which calls this.
      kinds.push({ kind: mainSeriesKind(getCandleType()) })
    }
    // Always-on volume series, between the candlestick and the agent overlays.
    if (volumeSeriesRef.current !== null) kinds.push({ kind: 'volume' })
    if (volumeMaSeriesRef.current !== null) kinds.push({ kind: 'volume_ma' })
    if (vwapSeriesRef.current !== null) kinds.push({ kind: 'vwap' })
    if (obvSeriesRef.current !== null) kinds.push({ kind: 'obv' })
    for (const { spec } of overlaySeriesRef.current.values()) {
      kinds.push({ kind: spec.kind, period: spec.period ?? null })
    }
    window.__test_chart_render__ = {
      seriesCount: kinds.length,
      seriesKinds: kinds,
      barCount: seriesRef.current !== null ? barCountRef.current : 0,
    }
  }, [])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    // lightweight-charts hands these strings to canvas APIs that don't resolve
    // CSS variables; passing `var(--chart-up)` paints with the browser's
    // invalid-color fallback. Resolve every token (⊕ user overrides + widths) to
    // concrete values at mount (and again on theme/style change in the effect
    // below). The ref gives the current theme without making this effect re-run.
    const style = resolveChartStyle(container, effectiveThemeRef.current)
    const colors = chartColorsFrom(style)

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: colors.text,
      },
      grid: {
        vertLines: { color: colors.border },
        horzLines: { color: colors.border },
      },
      timeScale: {
        timeVisible: false,
        secondsVisible: false,
      },
      autoSize: true,
    })
    const series = createMainSeries(chart, candleType)
    applyMainColors(series, candleType, colors)

    // Always-on volume series (Plan 0027 phase 3). Created once at mount; their
    // data is pushed in the bars effect. Disposed by `chart.remove()` on unmount
    // alongside the candlestick (the chart owns all its series).
    const volumeSeries = chart.addHistogramSeries({
      priceScaleId: VOLUME_SCALE_ID,
      color: colors.volume,
      priceFormat: { type: 'volume' },
      priceLineVisible: false,
      lastValueVisible: false,
    })
    const volumeMaSeries = chart.addLineSeries({
      priceScaleId: VOLUME_SCALE_ID,
      color: colors.volumeMa,
      lineWidth: style.widths.volumeMa as LineWidth,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    const vwapSeries = chart.addLineSeries({
      priceScaleId: PRICE_SCALE_ID, // rides the main price scale alongside candles
      color: colors.vwap,
      lineWidth: style.widths.vwap as LineWidth,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    const obvSeries = chart.addLineSeries({
      priceScaleId: OBV_SCALE_ID,
      color: colors.obv,
      lineWidth: style.widths.obv as LineWidth,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    // Push the candles into the upper band and dock each derived band on its own
    // overlay scale, so the volume/OBV strips don't share the price axis.
    chart.priceScale(PRICE_SCALE_ID).applyOptions({ scaleMargins: PRICE_SCALE_MARGINS })
    chart.priceScale(VOLUME_SCALE_ID).applyOptions({ scaleMargins: VOLUME_SCALE_MARGINS })
    chart.priceScale(OBV_SCALE_ID).applyOptions({ scaleMargins: OBV_SCALE_MARGINS })

    // Attach the pattern-span band primitive once (Plan 0049 phase 7). It draws
    // nothing until the spans effect feeds it spans; `chart.remove()` detaches it.
    const spanPrimitive = new PatternSpanPrimitive({
      bullish: colors.markerBullish,
      bearish: colors.markerBearish,
      neutral: colors.markerNeutral,
    })
    series.attachPrimitive(spanPrimitive)
    spanPrimitiveRef.current = spanPrimitive

    // Attach the trendline primitive once, here (not in `useTrendlines`), so it
    // rides the live series for the chart's whole life and is disposed by
    // `chart.remove()` — the same lifecycle as the span band (Plan 0064 fix).
    const trendlinePrimitive = new TrendlinePrimitive(readTrendlineColors(container))
    series.attachPrimitive(trendlinePrimitive)
    trendlinePrimitiveRef.current = trendlinePrimitive

    chartRef.current = chart
    seriesRef.current = series
    volumeSeriesRef.current = volumeSeries
    volumeMaSeriesRef.current = volumeMaSeries
    vwapSeriesRef.current = vwapSeries
    obvSeriesRef.current = obvSeries
    // Capture the Map reference into a local for the cleanup closure
    // (react-hooks/exhaustive-deps: ref.current may change between effect
    // run and cleanup invocation; the local capture is the canonical fix).
    const overlayMap = overlaySeriesRef.current
    const priceLineMap = priceLinesRef.current
    const supertrendMap = supertrendSeriesRef.current
    syncTestRenderHook()

    return () => {
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
      spanPrimitiveRef.current = null
      trendlinePrimitiveRef.current = null
      volumeSeriesRef.current = null
      volumeMaSeriesRef.current = null
      vwapSeriesRef.current = null
      obvSeriesRef.current = null
      overlayMap.clear()
      // The chart owns its price lines (disposed by chart.remove); drop our refs.
      priceLineMap.clear()
      supertrendMap.clear()
      syncTestRenderHook()
    }
    // `candleType` rebuilds the chart (series type is fixed at creation); the data
    // / marker / primitive effects + chart-subscribing hooks key on it too, so they
    // re-run and re-attach to the fresh series in the same commit (Plan 0068 ph4).
  }, [syncTestRenderHook, candleType])

  useEffect(() => {
    const chart = chartRef.current
    const candlestick = seriesRef.current
    if (!chart || !candlestick) return

    // Scroll-anchored prepend (Plan 0030): if `bars` grew on the LEFT (older
    // bars were prepended), capture the visible logical range *before* replacing
    // the data so we can shift it right by the number of prepended bars — the
    // viewport stays on the same bars instead of jumping. Any other change
    // (initial load, symbol/range change, forward growth) keeps the existing
    // fit-on-update behavior.
    const newFirstMs = bars.length > 0 ? new Date(bars[0].event_ts).getTime() : null
    const prevFirstMs = prevFirstTsRef.current
    const grewOnLeft = prevFirstMs !== null && newFirstMs !== null && newFirstMs < prevFirstMs
    const rangeBeforePrepend = grewOnLeft ? chart.timeScale().getVisibleLogicalRange() : null

    setMainData(candlestick, candleType, bars)
    barCountRef.current = bars.length

    // Always-on volume series, derived client-side from the same `bars`. Empty
    // `bars` yields empty arrays (no NaN/Infinity reaches lightweight-charts).
    volumeSeriesRef.current?.setData(computeVolumeBars(bars))
    volumeMaSeriesRef.current?.setData(computeVolumeMa(bars, VOLUME_MA_PERIOD))
    vwapSeriesRef.current?.setData(computeVwap(bars, VWAP_PERIOD))
    obvSeriesRef.current?.setData(computeObv(bars))

    // Resolve the style once for any series this reconcile CREATES (initial colour
    // + width, overrides layered). Existing series get re-applied in place by the
    // style/theme effect below, so a theme flip doesn't need to re-run this effect.
    const overlayStyle = containerRef.current
      ? resolveChartStyle(containerRef.current, effectiveThemeRef.current)
      : null

    const desired = new Map<string, OverlaySpec>()
    for (const spec of overlays ?? []) {
      // price_line overlays are horizontal lines, reconciled in the price-line
      // effect below — not line series, and not an "unsupported" warning case.
      if (spec.kind === 'price_line') continue
      // supertrend is a two-masked-series overlay, reconciled separately below —
      // skip the generic single-series path (and its "unsupported" warning).
      if (spec.kind === 'supertrend') continue
      if (!isSupportedOverlay(spec.kind)) {
        console.warn(
          `[CandlestickChart] unsupported overlay kind "${spec.kind}" — ignored (MVP renders ema/sma only)`,
        )
        continue
      }
      // A layer toggled off in the legend is removed below (absent from `desired`)
      // and re-added when re-checked.
      if (hidden.has(overlayLayerId(spec))) continue
      desired.set(overlayKey(spec), spec)
    }

    // Remove series no longer requested.
    for (const [key, entry] of overlaySeriesRef.current) {
      if (!desired.has(key)) {
        chart.removeSeries(entry.series)
        overlaySeriesRef.current.delete(key)
      }
    }

    // Add new series + recompute data for all kept ones (bars may have moved).
    for (const [key, spec] of desired) {
      let entry = overlaySeriesRef.current.get(key)
      if (entry === undefined) {
        const color = overlayStyle ? overlayStyleColor(spec, overlayStyle) : overlayColorFor(spec)
        const series = chart.addLineSeries({
          color,
          lineWidth: overlayStyle
            ? overlayStyleWidth(spec, overlayStyle)
            : DEFAULT_OVERLAY_LINE_WIDTH,
          priceLineVisible: false,
          lastValueVisible: false,
        })
        entry = { spec, series }
        overlaySeriesRef.current.set(key, entry)
      }
      entry.series.setData(computeOverlayData(bars, spec))
    }

    // Supertrend reconcile (Plan 0049 phase 9): each supertrend overlay draws two
    // masked line series (up=bullish, down=bearish) so the line flips colour at
    // trend changes. Same add/remove/toggle discipline as the generic overlays.
    const upColor = overlayStyle?.colors.markerBullish ?? DEFAULT_MARKER_COLORS.bullish
    const downColor = overlayStyle?.colors.markerBearish ?? DEFAULT_MARKER_COLORS.bearish
    const desiredSt = new Map<string, OverlaySpec>()
    for (const spec of overlays ?? []) {
      if (spec.kind !== 'supertrend') continue
      if (hidden.has(overlayLayerId(spec))) continue
      desiredSt.set(overlayKey(spec), spec)
    }
    for (const [key, entry] of supertrendSeriesRef.current) {
      if (!desiredSt.has(key)) {
        chart.removeSeries(entry.up)
        chart.removeSeries(entry.down)
        supertrendSeriesRef.current.delete(key)
      }
    }
    for (const [key, spec] of desiredSt) {
      let entry = supertrendSeriesRef.current.get(key)
      if (entry === undefined) {
        const lineOpts = { lineWidth: 2 as const, priceLineVisible: false, lastValueVisible: false }
        const up = chart.addLineSeries({ color: upColor, ...lineOpts })
        const down = chart.addLineSeries({ color: downColor, ...lineOpts })
        entry = { up, down }
        supertrendSeriesRef.current.set(key, entry)
      } else {
        entry.up.applyOptions({ color: upColor })
        entry.down.applyOptions({ color: downColor })
      }
      const points = computeSupertrend(bars, spec.period ?? 10, spec.multiplier ?? 3)
      const bands = supertrendBands(points)
      entry.up.setData(bands.up)
      entry.down.setData(bands.down)
    }

    const barsChanged = prevBarsRef.current !== bars
    if (grewOnLeft && rangeBeforePrepend && prevFirstMs !== null) {
      let prepended = 0
      for (const b of bars) {
        if (new Date(b.event_ts).getTime() < prevFirstMs) prepended += 1
        else break
      }
      chart.timeScale().setVisibleLogicalRange({
        from: (rangeBeforePrepend.from + prepended) as Logical,
        to: (rangeBeforePrepend.to + prepended) as Logical,
      })
    } else if (barsChanged) {
      // Only a genuine data change refits — NOT an overlay add or a legend toggle
      // (those re-run this effect via `overlays`/`hidden` but leave `bars` intact).
      chart.timeScale().fitContent()
    }
    prevBarsRef.current = bars
    prevFirstTsRef.current = newFirstMs
    syncTestRenderHook()
    // `candleType` re-runs this after a rebuild so the fresh main series (and the
    // recreated overlay/supertrend series) get their data pushed (Plan 0068 ph4).
  }, [bars, overlays, hidden, syncTestRenderHook, candleType])

  // Live forming-bar update (Plan 0049 phase 10): feed the already-polled `/quote`
  // into the chart's CURRENT (forming) bar via `series.update()` — close tracks
  // the quote, high/low extend — but ONLY when the quote's `as_of` falls within
  // the latest bar's period. A quote that predates the latest bar, or has crossed
  // into a not-yet-fetched new period, touches nothing: we never rewrite a closed
  // bar nor fabricate a new one (that is a refetch/SSE concern). No new fetch, no
  // setData — `series.update()` at the last bar's time updates it in place. No
  // lookahead: this is the live current bar, not historical replay.
  useEffect(() => {
    const series = seriesRef.current
    if (!series || !quote || bars.length === 0) return
    const periodMs = timeframeDurationMs(timeframe)
    if (periodMs === null) return
    const last = bars[bars.length - 1]
    const lastStartMs = new Date(last.event_ts).getTime()
    const asOfMs = new Date(quote.as_of).getTime()
    // Outside the forming bar's [start, start + period) window → leave every bar.
    if (asOfMs < lastStartMs || asOfMs >= lastStartMs + periodMs) return
    const time = Math.floor(lastStartMs / 1000) as UTCTimestamp
    if (candleType === 'line' || candleType === 'area') {
      // Line/area track a single value (the forming close).
      ;(series as ISeriesApi<'Line'>).update({ time, value: quote.price })
    } else {
      ;(series as ISeriesApi<'Candlestick'>).update({
        time,
        open: last.open,
        high: Math.max(last.high, quote.price),
        low: Math.min(last.low, quote.price),
        close: quote.price,
      })
    }
  }, [quote, bars, timeframe, candleType])

  // Monthly axis ticks (Plan 0050 phase 7): the `1mo` timeframe needs month/year
  // tick marks, not the day-level labels lightweight-charts' default emits at some
  // zooms (which read as repeated "1" day numbers on month-spaced bars). Scoped to
  // `1mo` only — every other timeframe keeps the library default. The chart
  // unmounts during the loading state on a timeframe change (OhlcvView gates it
  // behind `!isLoading`), so each timeframe gets a fresh chart and this runs once
  // per mount; the `else` branch is just belt-and-suspenders if that ever changes.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    chart.applyOptions({
      timeScale: {
        tickMarkFormatter: timeframe === '1mo' ? monthlyTickMarkFormatter : undefined,
      },
    })
    // Re-apply on a rebuild (Plan 0068 ph4) — the fresh chart needs the formatter.
  }, [timeframe, candleType])

  // Pointer-gesture state machine + agent-mode POSTs (Plan 0029 phase 1).
  // Called AFTER the chart-creation effect so its gesture effect sees a
  // populated `chartRef`/`seriesRef` on mount.
  const { selectRangeMode, toggleSelectRange, selection, rangeLabel, clickedBarTs } =
    useChartGestures(containerRef, chartRef, seriesRef, {
      agentMode: agentModeEnabled,
      symbol,
      timeframe,
      bars,
    })

  // Lazy backward paging (Plan 0030): ask the parent for older bars when the
  // user scrolls near the left edge. A sibling concern to the pointer gestures
  // (it is not a pointer gesture), and likewise called after the chart-creation
  // effect so `chartRef` is populated on mount.
  useLazyHistoryTrigger(chartRef, {
    enabled: historyTriggerEnabled && onReachLeftEdge !== undefined,
    onReachLeftEdge: onReachLeftEdge ?? NOOP,
    rebuildToken: candleType,
  })

  // Recompute chart-pattern trendlines on mount + debounced visible-range settle
  // (Plan 0064 phase 5, ADR-0059) so the lines are re-derived for the bars on
  // screen and return after a reload. Called after the chart-creation effect so
  // `chartRef` is populated on mount; gated off until symbol+timeframe are known.
  useChartPatternRecompute(chartRef, {
    enabled: symbol !== undefined && timeframe !== undefined,
    onRecompute: () => {
      void recomputeTrendlines()
    },
    rebuildToken: candleType,
  })

  // Trendline overlay primitive (Plan 0052 phase 4, ADR-0049). The primitive is
  // attached in the chart-creation effect above (Plan 0064 fix); this hook only
  // FEEDS it specs/colours/visibility. Called after that effect so the ref is
  // populated on mount.
  useTrendlines(containerRef, trendlinePrimitiveRef, {
    trendlines: shownTrendlines,
    highlightKey: highlightedTrendlineKey,
    effectiveTheme,
    rebuildToken: candleType,
  })

  // Markers (annotation markers + the clicked-bar affordance) are themed: their
  // colors resolve from the DOM tokens, so they recolor when `effectiveTheme`
  // changes. Built in an effect (not useMemo) so the container is mounted and
  // the tokens resolve; re-set on annotation / clicked-bar / theme change.
  useEffect(() => {
    const series = seriesRef.current
    const container = containerRef.current
    if (!series || !container) return
    const colors = chartColorsFrom(resolveChartStyle(container, effectiveTheme))
    // Drop annotations whose marker-direction layer is toggled off in the legend.
    const visibleAnnotations = (annotations ?? []).filter((a) => !hidden.has(markerLayerId(a.kind)))
    const base = annotationsToMarkers(
      visibleAnnotations,
      {
        bullish: colors.markerBullish,
        bearish: colors.markerBearish,
        neutral: colors.markerNeutral,
      },
      // Glyph-only on the canvas; the label shows in the backed hover tooltip
      // (Plan 0049 phase 12 — no bare overlapping text over the candles).
      { includeText: false },
    )
    let markers = base
    if (clickedBarTs !== null) {
      const time = Math.floor(new Date(clickedBarTs).getTime() / 1000) as UTCTimestamp
      const clicked: SeriesMarker<UTCTimestamp> = {
        time,
        position: 'aboveBar',
        shape: 'circle',
        color: colors.markerClicked,
        text: clickedBarTs.slice(0, 10),
      }
      // setMarkers requires ascending time order.
      markers = [...base, clicked].sort((a, b) => (a.time as number) - (b.time as number))
    }
    series.setMarkers(markers)
  }, [annotations, clickedBarTs, effectiveTheme, hidden, styleVersion, candleType])

  // Multi-bar pattern span band (Plan 0049 phase 7): feed the span primitive the
  // current spans, theme-resolved direction colours, and the legend visibility.
  // The primitive redraws via `requestUpdate`; the band tracks pan/zoom for free
  // (the chart re-reads `paneViews`). Recolours in place on a theme flip — no
  // remount (the deps include `effectiveTheme`, the primitive persists).
  useEffect(() => {
    const primitive = spanPrimitiveRef.current
    const container = containerRef.current
    if (!primitive || !container) return
    const colors = chartColorsFrom(resolveChartStyle(container, effectiveTheme))
    primitive.setColors({
      bullish: colors.markerBullish,
      bearish: colors.markerBearish,
      neutral: colors.markerNeutral,
    })
    primitive.setSpans(markersToSpans(annotations ?? []))
    primitive.setVisible(!hidden.has(SPAN_LAYER_ID))
    // `candleType` re-feeds the freshly-attached span primitive after a rebuild.
  }, [annotations, effectiveTheme, hidden, styleVersion, candleType])

  // Price lines (Plan 0047 phase 9): reconcile horizontal `price_line` overlays
  // (S/R levels the agent pushes) on the candlestick series. A line toggled off
  // in the legend is removed; re-checking re-creates it. Colours resolve from the
  // theme tokens, so a theme flip recolours the kept lines in place.
  useEffect(() => {
    const series = seriesRef.current
    const container = containerRef.current
    if (!series || !container) return
    const colors = chartColorsFrom(resolveChartStyle(container, effectiveTheme))
    const desired = new Map<string, OverlaySpec>()
    for (const spec of overlays ?? []) {
      if (spec.kind !== 'price_line') continue
      if (hidden.has(priceLineId(spec))) continue
      desired.set(priceLineId(spec), spec)
    }
    for (const [id, line] of priceLinesRef.current) {
      if (!desired.has(id)) {
        series.removePriceLine(line)
        priceLinesRef.current.delete(id)
      }
    }
    for (const [id, spec] of desired) {
      const color = priceLineColor(spec, colors)
      const existing = priceLinesRef.current.get(id)
      if (existing === undefined) {
        const line = series.createPriceLine({
          price: spec.price ?? 0,
          color,
          axisLabelVisible: true,
          title: spec.label ?? '',
        })
        priceLinesRef.current.set(id, line)
      } else {
        existing.applyOptions({ color })
      }
    }
    // `candleType` re-creates the price lines on the fresh series after a rebuild.
  }, [overlays, hidden, effectiveTheme, styleVersion, candleType])

  // Build the layers-legend descriptor list (Plan 0047 phase 9): one row per
  // indicator overlay, per marker-direction group present, and per price line —
  // each with its resolved colour (equal to the colour the layer is drawn with)
  // and its current visibility. Recomputed when the inputs or theme change.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const style = resolveChartStyle(container, effectiveTheme)
    const colors = chartColorsFrom(style)
    const next: ChartLayer[] = []
    for (const spec of overlays ?? []) {
      if (spec.kind === 'price_line' || !isSupportedOverlay(spec.kind)) continue
      const id = overlayLayerId(spec)
      next.push({
        id,
        label: overlayLabel(spec),
        color: overlayStyleColor(spec, style),
        kind: 'overlay',
        visible: !hidden.has(id),
        // The overlay kind keys the glossary tooltip (Plan 0065) — ema/sma/
        // supertrend resolve; a future unsupported kind degrades to plain text.
        glossaryKey: spec.kind,
      })
    }
    for (const direction of new Set((annotations ?? []).map((a) => a.kind))) {
      const id = markerLayerId(direction)
      const markerColor =
        direction === 'bullish_marker'
          ? colors.markerBullish
          : direction === 'bearish_marker'
            ? colors.markerBearish
            : colors.markerNeutral
      next.push({
        id,
        label: markerLayerLabel(direction),
        color: markerColor,
        kind: 'marker',
        visible: !hidden.has(id),
      })
    }
    for (const spec of overlays ?? []) {
      if (spec.kind !== 'price_line') continue
      const id = priceLineId(spec)
      next.push({
        id,
        label: priceLineLabel(spec),
        color: priceLineColor(spec, colors),
        kind: 'price_line',
        visible: !hidden.has(id),
      })
    }
    // One row for ALL multi-bar pattern spans (Plan 0049 phase 7); only present
    // when at least one span exists. Unchecking it hides every span box and
    // leaves the arrows/overlays untouched (the spans effect reads `hidden`).
    if (markersToSpans(annotations ?? []).length > 0) {
      next.push({
        id: SPAN_LAYER_ID,
        label: SPAN_LAYER_LABEL,
        color: colors.markerNeutral,
        kind: 'span',
        visible: !hidden.has(SPAN_LAYER_ID),
      })
    }
    // Grouped trendline rows (Plan 0067 phase 3 / ADR-0061): one row per
    // (pattern type, state) present, each with its type-colour swatch, instance
    // count, per-group visibility, and a highlight key for hover. Built from the
    // deduped set so a hidden group still lists (to re-enable). Replaces the
    // single "Trendlines" row.
    const trendlineColors = readTrendlineColors(container)
    const groups = new Map<
      string,
      { pattern: string | null | undefined; style: TrendlineSpec['style']; count: number }
    >()
    for (const spec of visibleTrendlines) {
      const key = patternStateKey(spec)
      const existing = groups.get(key)
      if (existing) existing.count += 1
      else groups.set(key, { pattern: spec.pattern, style: spec.style, count: 1 })
    }
    for (const [key, group] of groups) {
      const id = trendlineGroupLayerId(key)
      next.push({
        id,
        label: `${patternDisplayName(group.pattern)} (${trendlineStateLabel(group.style)})`,
        color: trendlineColor(group.pattern, trendlineColors),
        kind: 'trendline',
        visible: !hidden.has(id),
        count: group.count,
        highlightKey: key,
      })
    }
    setLayers(next)
  }, [overlays, annotations, visibleTrendlines, hidden, effectiveTheme, styleVersion])

  // Hover tooltip (Plan 0047 phase 8): on crosshair move, show a labelled
  // marker's text and/or each overlay line's name + value at that bar. Reads only
  // data already in renderer state (annotations + the overlay readings the chart
  // pulls from `seriesData`) — no sidecar call. Re-subscribes when `annotations`
  // change so the handler closes over the current list; overlay series are read
  // from the ref, so an overlay change needs no re-subscribe.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const handler = (param: MouseEventParams): void => {
      // A trendline can extend past the last bar, so the pointer may be over a
      // line while `param.time` is undefined — gate only on `point`, and compute
      // the time-keyed marker/overlay content only when a time is present.
      if (param.point === undefined) {
        setTooltip(null)
        return
      }
      const readings: OverlayReading[] = []
      if (param.time !== undefined) {
        for (const { spec, series } of overlaySeriesRef.current.values()) {
          const datum = param.seriesData.get(series)
          const value = datum !== undefined ? (datum as LineData).value : undefined
          if (typeof value === 'number') {
            readings.push({ label: overlayLabel(spec), value })
          }
        }
      }
      const timeContent =
        param.time !== undefined
          ? tooltipAtTime(param.time as UTCTimestamp, annotations ?? [], readings)
          : null
      // Trendline under the cursor (Plan 0067 phase 2): the primitive hit-tests
      // the hovered pixel against its drawn segments and returns the spec, if any.
      const hovered =
        trendlinePrimitiveRef.current?.hitTestTrendline(param.point.x, param.point.y) ?? null
      const trendlines = hovered ? [trendlineTooltipText(hovered)] : []
      const markers = timeContent?.markers ?? []
      const overlays = timeContent?.overlays ?? []
      if (markers.length === 0 && overlays.length === 0 && trendlines.length === 0) {
        setTooltip(null)
        return
      }
      setTooltip({
        content: { markers, overlays, trendlines },
        x: param.point.x,
        y: param.point.y,
      })
    }
    chart.subscribeCrosshairMove(handler)
    return () => chart.unsubscribeCrosshairMove(handler)
    // `candleType` re-subscribes the crosshair handler on the fresh chart (ph4).
  }, [annotations, candleType])

  // Re-apply the EXISTING chart's colours + line widths when the effective theme
  // changes OR the user mutates the chart-style store (Plan 0068 phase 2). One
  // `resolveChartStyle` pass, pushed via `applyOptions` — no remount (the creation
  // effect's deps are `[syncTestRenderHook]`); also runs once on mount, idempotent
  // with the creation values. Colour AND width both flow here, so a colour or a
  // width override lands in place on any mounted chart.
  useEffect(() => {
    const container = containerRef.current
    const chart = chartRef.current
    const candlestick = seriesRef.current
    if (!container || !chart || !candlestick) return
    const style = resolveChartStyle(container, effectiveTheme)
    const colors = chartColorsFrom(style)
    chart.applyOptions({
      layout: { textColor: colors.text },
      grid: {
        vertLines: { color: colors.border },
        horzLines: { color: colors.border },
      },
    })
    applyMainColors(candlestick, candleType, colors)
    volumeSeriesRef.current?.applyOptions({ color: colors.volume })
    volumeMaSeriesRef.current?.applyOptions({
      color: colors.volumeMa,
      lineWidth: style.widths.volumeMa as LineWidth,
    })
    vwapSeriesRef.current?.applyOptions({
      color: colors.vwap,
      lineWidth: style.widths.vwap as LineWidth,
    })
    obvSeriesRef.current?.applyOptions({
      color: colors.obv,
      lineWidth: style.widths.obv as LineWidth,
    })
    for (const { spec, series } of overlaySeriesRef.current.values()) {
      series.applyOptions({
        color: overlayStyleColor(spec, style),
        lineWidth: overlayStyleWidth(spec, style),
      })
    }
    // Supertrend's two masked series recolor from the bull/bear tokens in place.
    for (const { up, down } of supertrendSeriesRef.current.values()) {
      up.applyOptions({ color: colors.markerBullish })
      down.applyOptions({ color: colors.markerBearish })
    }
  }, [effectiveTheme, styleVersion, candleType])

  // Track the effective theme; the subscription fires on an explicit theme
  // change and on an OS flip while in `system` mode. Unsubscribes on unmount.
  useEffect(() => subscribeEffective(setEffectiveTheme), [])

  // Re-resolve + re-apply on any chart-style store mutation (colour, width, or
  // candle-type) by bumping the version the restyle effect keys on. Unsubscribes
  // on unmount.
  useEffect(() => subscribeChartStyle(() => setStyleVersion((v) => v + 1)), [])

  return (
    <div className={styles.wrapper}>
      <div className={styles.controls}>
        {agentModeEnabled && (
          <button
            type="button"
            data-testid="select-range-toggle"
            aria-pressed={selectRangeMode}
            className={styles.selectRangeButton}
            onClick={toggleSelectRange}
          >
            {selectRangeMode ? t('chart.selectingRange') : t('chart.selectRange')}
          </button>
        )}
        <button
          type="button"
          data-testid="scan-patterns-button"
          className={styles.scanButton}
          onClick={scanVisibleRange}
          disabled={scanStatus.kind === 'scanning' || !symbol || !timeframe}
        >
          {scanStatus.kind === 'scanning' ? t('chart.scanning') : t('chart.scanPatterns')}
        </button>
        {scanStatus.kind === 'done' && (
          <span data-testid="scan-patterns-status" className={styles.scanStatus}>
            {t('chart.patternCount', { count: scanStatus.count })}
          </span>
        )}
        {scanStatus.kind === 'empty' && (
          <span data-testid="scan-patterns-status" className={styles.scanStatus}>
            {t('chart.noPatternsInView')}
          </span>
        )}
        {scanStatus.kind === 'error' && (
          <span data-testid="scan-patterns-error" role="alert" className={styles.scanError}>
            {scanStatus.message}
          </span>
        )}
        <button
          type="button"
          data-testid="scan-chart-patterns-button"
          className={styles.scanButton}
          onClick={scanChartPatternsVisibleRange}
          disabled={chartScanStatus.kind === 'scanning' || !symbol || !timeframe}
        >
          {chartScanStatus.kind === 'scanning' ? t('chart.scanning') : t('chart.scanChartPatterns')}
        </button>
        {chartScanStatus.kind === 'done' && (
          <span data-testid="scan-chart-patterns-status" className={styles.scanStatus}>
            {t('chart.patternCount', { count: chartScanStatus.count })}
          </span>
        )}
        {chartScanStatus.kind === 'empty' && (
          <span data-testid="scan-chart-patterns-status" className={styles.scanStatus}>
            {t('chart.noChartPatternsInView')}
          </span>
        )}
        {chartScanStatus.kind === 'error' && (
          <span data-testid="scan-chart-patterns-error" role="alert" className={styles.scanError}>
            {chartScanStatus.message}
          </span>
        )}
      </div>
      <div className={styles.chartArea}>
        <div
          ref={containerRef}
          className={`${styles.chartContainer} ${selectRangeMode ? styles.selectRangeActive : ''}`.trim()}
          data-testid="candlestick-chart"
          role="img"
          aria-label={ariaLabel ?? t('chart.ariaLabel', { count: bars.length })}
        />
        {selection && (
          <div
            className={styles.selectionOverlay}
            data-testid="range-selection-overlay"
            aria-hidden="true"
            style={{
              left: Math.min(selection.startX, selection.endX),
              width: Math.abs(selection.endX - selection.startX),
            }}
          />
        )}
        {selection && rangeLabel && (
          <div
            className={styles.selectionLabel}
            data-testid="range-selection-label"
            style={{ left: Math.min(selection.startX, selection.endX) }}
          >
            {formatRangeLabel(rangeLabel.start, rangeLabel.end)}
          </div>
        )}
        {tooltip && (
          <ChartTooltip
            content={tooltip.content}
            x={tooltip.x}
            y={tooltip.y}
            containerWidth={containerRef.current?.clientWidth ?? 0}
            containerHeight={containerRef.current?.clientHeight ?? 0}
          />
        )}
        <LayersPanel
          layers={layers}
          onToggle={toggleLayer}
          onHighlight={setHighlightedTrendlineKey}
        />
      </div>
    </div>
  )
}
