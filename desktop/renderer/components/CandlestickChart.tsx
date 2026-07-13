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
import { ColorType, HistogramSeries, LineSeries, createChart } from 'lightweight-charts'
import type { IChartApi, ISeriesApi, LineWidth, Logical } from 'lightweight-charts'

import type { IPriceLine } from 'lightweight-charts'

import { t } from '../lib/i18n'
import { useChartGestures } from '../hooks/useChartGestures'
import { useChartMarkers } from '../hooks/useChartMarkers'
import { useChartPatternRecompute } from '../hooks/useChartPatternRecompute'
import { useChartRestyle } from '../hooks/useChartRestyle'
import { useChartScans } from '../hooks/useChartScans'
import { useChartTooltip } from '../hooks/useChartTooltip'
import { useCandleMarkerGroups } from '../hooks/useCandleMarkerGroups'
import { useFormingBar } from '../hooks/useFormingBar'
import { useLayersLegend } from '../hooks/useLayersLegend'
import { useLazyHistoryTrigger } from '../hooks/useLazyHistoryTrigger'
import { useBbandsSeries } from '../hooks/useBbandsSeries'
import { useIchimokuSeries } from '../hooks/useIchimokuSeries'
import { useOscillatorPanes, type OscillatorPaneEntry } from '../hooks/useOscillatorPanes'
import { useOverlaySeries } from '../hooks/useOverlaySeries'
import { usePriceLines } from '../hooks/usePriceLines'
import { useSupertrendSeries } from '../hooks/useSupertrendSeries'
import type { ChartMarker } from '../lib/markers'
import { candleGroupKeyFromLayerId } from '../lib/candleGroups'
import { ChartToolbar } from './ChartToolbar'
import { ChartTooltip } from './ChartTooltip'
import { LayersPanel } from './LayersPanel'
import {
  OBV_LAYER_ID,
  OBV_PANE_HEIGHT,
  OBV_PANE_ID,
  OBV_SCALE_ID,
  PRICE_SCALE_ID,
  PRICE_SCALE_MARGINS,
  VOLUME_SCALE_ID,
  VOLUME_SCALE_MARGINS,
  applyMainColors,
  chartColorsFrom,
  createMainSeries,
  mainSeriesKind,
  setMainData,
  type MainSeries,
  type OverlayEntry,
} from '../lib/chartSeries'
import { formatRangeLabel, monthlyTickMarkFormatter } from '../lib/chartAxis'
import { IchimokuPrimitive, readIchimokuColors } from '../lib/ichimoku'
import { PaneRegistry } from '../lib/panes'
import { PatternSpanPrimitive } from '../lib/spans'
import {
  TrendlinePrimitive,
  dedupeTrendlines,
  patternStateKey,
  readTrendlineColors,
  trendlineGroupLayerId,
} from '../lib/trendlines'
import { useTrendlines } from '../hooks/useTrendlines'
import { DivergencePrimitive, readDivergenceColors } from '../lib/divergences'
import { useDivergences, requiredOscillatorKindsFor } from '../hooks/useDivergences'
import {
  getStoredTheme,
  resolveEffective,
  subscribeEffective,
  type EffectiveTheme,
} from '../lib/theme'
import { getCandleType, resolveChartStyle, subscribeChartStyle } from '../lib/chartStyle'
import {
  addUserOverlay,
  getUserOverlaysSnapshot,
  mergeOverlays,
  removeUserOverlay,
  subscribeUserOverlays,
  userOverlayStoreKey,
} from '../lib/userOverlays'
import { overlayLayerId } from '../lib/overlays'
import {
  VOLUME_MA_PERIOD,
  VWAP_PERIOD,
  computeObv,
  computeVolumeBars,
  computeVolumeMa,
  computeVwap,
} from '../lib/volume'
import type { Bar } from '../types/sidecar/bar'
import type { Divergence, OverlaySpec, TrendlineSpec } from '../types/events'
import type { QuoteResponse } from '../types/sidecar/quote-response'
import styles from './CandlestickChart.module.css'

// Stable no-op for the lazy-history trigger when no `onReachLeftEdge` is wired
// (keeps the trigger hook's callback ref from churning on every render).
const NOOP = (): void => {}

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

// Stable empty divergence list (same re-render-stability rationale as trendlines).
const NO_DIVERGENCES: ReadonlyArray<Divergence> = []

// Stable empty user-overlay list for charts with no (symbol, timeframe) — a fresh
// `[]` per render would re-run the merge memo every time.
const NO_USER_OVERLAYS: OverlaySpec[] = []

interface Props {
  bars: Bar[]
  annotations?: ChartMarker[]
  overlays?: ReadonlyArray<OverlaySpec>
  /** Plan 0052 phase 4 (ADR-0049): sloped trendlines (necklines, triangle/wedge
   * bounds) from `chart.show`/`chart.update`, drawn by the trendline primitive
   * via `useTrendlines`. Dashed = forming, solid = confirmed. */
  trendlines?: ReadonlyArray<TrendlineSpec>
  /** Plan 0091 phase 9 (ADR-0090): price↔oscillator divergences from the dedicated
   * `chart.divergences` channel, drawn by `useDivergences` as two segments — price
   * pivots on pane 0, oscillator pivots on that oscillator's own pane. */
  divergences?: ReadonlyArray<Divergence>
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

export function CandlestickChart({
  bars,
  annotations,
  overlays,
  trendlines = NO_TRENDLINES,
  divergences = NO_DIVERGENCES,
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
  // Bollinger Bands overlays (Plan 0082 phase 2) draw as THREE line series
  // (upper/middle/lower) on the price pane, keyed by overlayKey; a legend toggle
  // removes all three. Fed by `useBbandsSeries` below.
  const bbandsSeriesRef = useRef<
    Map<
      string,
      { upper: ISeriesApi<'Line'>; middle: ISeriesApi<'Line'>; lower: ISeriesApi<'Line'> }
    >
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
  // Ichimoku overlay primitive (Plan 0073 phase 4, ADR-0067): attached at mount
  // like the span/trendline primitives so it rides the live series and is disposed
  // by `chart.remove()`. Draws the five lines + displaced filled cloud; fed by
  // `useIchimokuSeries` below.
  const ichimokuPrimitiveRef = useRef<IchimokuPrimitive | null>(null)
  // Divergence primitives (Plan 0091 phase 9, ADR-0090): the price-pane primitive
  // rides the candle series (draws price-pivot segments); the OBV-pane primitive
  // rides the OBV series (draws obv oscillator-pivot segments). Each oscillator
  // pane's own divergence primitive is attached by `useOscillatorPanes`. All fed by
  // `useDivergences` below, same lifecycle discipline as the trendline primitive.
  const divergencePricePrimitiveRef = useRef<DivergencePrimitive | null>(null)
  const obvDivergencePrimitiveRef = useRef<DivergencePrimitive | null>(null)
  // Always-on volume series (Plan 0027 phase 3).
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const volumeMaSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const vwapSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const obvSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  // Oscillator sub-panes (Plan 0091 phase 6): the pane registry (shared with OBV so
  // OBV stays pane 1) and the active-oscillator-pane map `useOscillatorPanes` owns.
  const paneRegistryRef = useRef<PaneRegistry | null>(null)
  const oscillatorPanesRef = useRef<Map<string, OscillatorPaneEntry>>(new Map())
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
  // User-originated overlays (Plan 0082 phase 3, ADR-0077): a renderer-owned layer
  // keyed by (symbol, timeframe), merged with the agent's `overlays` prop for
  // drawing + the legend. STICKY — an agent chart.show/update replaces only the
  // prop, never this store, so the user's indicators survive an agent redraw. The
  // snapshot is a stable ref (replaced on mutation), so the memos below recompute
  // only when the store, symbol, timeframe, or agent overlays actually change.
  const userOverlaysSnapshot = useSyncExternalStore(
    subscribeUserOverlays,
    getUserOverlaysSnapshot,
    getUserOverlaysSnapshot,
  )
  const userOverlays = useMemo(
    () =>
      symbol && timeframe
        ? (userOverlaysSnapshot[userOverlayStoreKey(symbol, timeframe)] ?? NO_USER_OVERLAYS)
        : NO_USER_OVERLAYS,
    [userOverlaysSnapshot, symbol, timeframe],
  )
  // `merged.userKeys` (the user-originated overlayKeys) is consumed by the legend
  // in phase 4 to branch remove-vs-hide; phase 3 draws the union.
  const merged = useMemo(() => mergeOverlays(overlays, userOverlays), [overlays, userOverlays])
  const effectiveOverlays = merged.overlays
  // Add / remove a user overlay (Plan 0082 phase 4). Only available when the chart
  // carries a (symbol, timeframe) to key the store by. Remove maps the legend row
  // id back to the stored spec via its overlayLayerId.
  const canAddOverlay = Boolean(symbol && timeframe)
  const handleAddOverlay = useCallback(
    (spec: OverlaySpec): void => {
      if (symbol && timeframe) addUserOverlay(symbol, timeframe, spec)
    },
    [symbol, timeframe],
  )
  const handleRemoveOverlay = useCallback(
    (id: string): void => {
      if (!symbol || !timeframe) return
      const spec = userOverlays.find((s) => overlayLayerId(s) === id)
      if (spec) removeUserOverlay(symbol, timeframe, spec)
    },
    [symbol, timeframe, userOverlays],
  )
  // Layers-legend state (Plan 0047 phase 9), all ephemeral: `hidden` is the set
  // of layer ids the user toggled off; `layers` is the resolved descriptor list
  // the panel renders. Reset on remount (no persistence) by construction.
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set())
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
  // Candlestick-marker groups (Plan 0071 phase 2): sweep markers grouped by
  // (pattern type, direction), draw-on-select selection, master gate, and hover
  // (Plan 0072 phase 8: `useCandleMarkerGroups` owns this state).
  const {
    candleGroups,
    enabledCandleGroups,
    drawnMarkers,
    highlightedCandleGroup,
    setHighlightedCandleGroup,
    toggleCandleGroup,
    candleKeySet,
  } = useCandleMarkerGroups(annotations, hidden)
  // LayersPanel routes a candlestick GROUP row (opt-in) to the enabled set and
  // everything else (overlays / candlestick master / price-lines / trendline
  // groups, all opt-out) to `hidden` — the glue joining the two legend systems.
  const onLayerToggle = useCallback(
    (id: string): void => {
      const groupKey = candleGroupKeyFromLayerId(id)
      if (groupKey !== null) toggleCandleGroup(groupKey)
      else toggleLayer(id)
    },
    [toggleCandleGroup, toggleLayer],
  )
  // Hover-highlight is shared by both legend systems: a candlestick group key
  // drives the marker emphasis, any other key drives the trendline primitive.
  const onLayerHighlight = useCallback(
    (key: string | null): void => {
      if (key !== null && candleKeySet.has(key)) {
        setHighlightedCandleGroup(key)
        setHighlightedTrendlineKey(null)
      } else {
        setHighlightedTrendlineKey(key)
        setHighlightedCandleGroup(null)
      }
    },
    [candleKeySet, setHighlightedCandleGroup],
  )
  // Pattern-scan triggers + their button status (Plan 0049 ph8 / Plan 0064 ph5),
  // sweeping the current visible range via the typed client (Plan 0072 phase 8:
  // `useChartScans`).
  const {
    scanStatus,
    chartScanStatus,
    scanVisibleRange,
    scanChartPatternsVisibleRange,
    recomputeTrendlines,
  } = useChartScans(chartRef, { symbol, timeframe })

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
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceScaleId: VOLUME_SCALE_ID,
      color: colors.volume,
      priceFormat: { type: 'volume' },
      priceLineVisible: false,
      lastValueVisible: false,
    })
    const volumeMaSeries = chart.addSeries(LineSeries, {
      priceScaleId: VOLUME_SCALE_ID,
      color: colors.volumeMa,
      lineWidth: style.widths.volumeMa as LineWidth,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    const vwapSeries = chart.addSeries(LineSeries, {
      priceScaleId: PRICE_SCALE_ID, // rides the main price scale alongside candles
      color: colors.vwap,
      lineWidth: style.widths.vwap as LineWidth,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    // OBV lives on its own REAL pane below the price pane (Plan 0095 phase 2, v5
    // `addPane()` via the pane registry) — no longer a `scaleMargins` band sharing
    // the price axis. Volume/VWAP stay on the price pane (pane 0).
    const paneRegistry = new PaneRegistry(chart)
    paneRegistryRef.current = paneRegistry // shared with the oscillator sub-panes (Plan 0091)
    const obvPaneIndex = paneRegistry.ensure(OBV_PANE_ID)
    const obvSeries = chart.addSeries(
      LineSeries,
      {
        // OBV's own (per-pane) overlay scale — keeps it a distinguishable always-on
        // series, not an agent overlay. No scaleMargins now: it owns the pane.
        priceScaleId: OBV_SCALE_ID,
        color: colors.obv,
        lineWidth: style.widths.obv as LineWidth,
        priceLineVisible: false,
        lastValueVisible: false,
      },
      obvPaneIndex,
    )
    paneRegistry.pane(OBV_PANE_ID)?.setHeight(OBV_PANE_HEIGHT)
    // Candles occupy the upper band of the price pane; volume hugs its bottom.
    chart.priceScale(PRICE_SCALE_ID).applyOptions({ scaleMargins: PRICE_SCALE_MARGINS })
    chart.priceScale(VOLUME_SCALE_ID).applyOptions({ scaleMargins: VOLUME_SCALE_MARGINS })

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

    // Attach the Ichimoku primitive once (Plan 0073 phase 4), same lifecycle as the
    // span/trendline primitives. It draws nothing until `useIchimokuSeries` feeds
    // it geometries; `chart.remove()` detaches it.
    const ichimokuPrimitive = new IchimokuPrimitive(readIchimokuColors(container))
    series.attachPrimitive(ichimokuPrimitive)
    ichimokuPrimitiveRef.current = ichimokuPrimitive

    // Divergence primitives (Plan 0091 phase 9, ADR-0090): the price-pane one on the
    // candle series (draws every divergence's price-pivot segment), the OBV one on
    // the OBV series (draws obv oscillator-pivot segments). Each oscillator pane's
    // own primitive is attached by `useOscillatorPanes`. All fed by `useDivergences`;
    // `chart.remove()` detaches these two.
    const divergenceColors = readDivergenceColors(container)
    const divergencePricePrimitive = new DivergencePrimitive('price', divergenceColors)
    series.attachPrimitive(divergencePricePrimitive)
    divergencePricePrimitiveRef.current = divergencePricePrimitive
    const obvDivergencePrimitive = new DivergencePrimitive('oscillator', divergenceColors)
    obvSeries.attachPrimitive(obvDivergencePrimitive)
    obvDivergencePrimitiveRef.current = obvDivergencePrimitive

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
    const bbandsMap = bbandsSeriesRef.current
    const oscillatorPanes = oscillatorPanesRef.current
    syncTestRenderHook()

    return () => {
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
      spanPrimitiveRef.current = null
      trendlinePrimitiveRef.current = null
      ichimokuPrimitiveRef.current = null
      divergencePricePrimitiveRef.current = null
      obvDivergencePrimitiveRef.current = null
      volumeSeriesRef.current = null
      volumeMaSeriesRef.current = null
      vwapSeriesRef.current = null
      obvSeriesRef.current = null
      // `chart.remove()` disposes the panes + their series; drop our bookkeeping so
      // the oscillator hook rebuilds them on the fresh chart (Plan 0091).
      paneRegistryRef.current = null
      oscillatorPanes.clear()
      overlayMap.clear()
      // The chart owns its price lines (disposed by chart.remove); drop our refs.
      priceLineMap.clear()
      supertrendMap.clear()
      bbandsMap.clear()
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
    // `candleType` re-runs this after a rebuild so the fresh main series gets its
    // data pushed (Plan 0068 ph4). Overlay/supertrend reconcile lives in its own
    // hook now (Plan 0072 phase 8), so this effect no longer keys on overlays/hidden.
  }, [bars, syncTestRenderHook, candleType])

  // OBV visibility (Plan 0076 phase 2): the always-on OBV series (Plan 0027, now on
  // its own real pane — Plan 0095 ph2) is toggleable from the layers legend. Hiding
  // it blanks the series in place; its pane is retained.
  // Keyed on `candleType` so a rebuild's fresh series re-applies the current toggle.
  useEffect(() => {
    obvSeriesRef.current?.applyOptions({ visible: !hidden.has(OBV_LAYER_ID) })
  }, [hidden, candleType])

  // Agent-overlay line series + supertrend two-series reconcile (Plan 0007 ph4.5 /
  // Plan 0049 ph9), split out of the bars effect (Plan 0072 phase 8). Defined
  // AFTER the bars effect so they run after `setMainData` on each commit; each
  // reads the theme off the ref so a flip recolours in place (restyle effect)
  // rather than re-creating series.
  useOverlaySeries(chartRef, containerRef, overlaySeriesRef, {
    bars,
    overlays: effectiveOverlays,
    hidden,
    effectiveThemeRef,
    rebuildToken: candleType,
    syncTestRenderHook,
  })
  useSupertrendSeries(chartRef, containerRef, supertrendSeriesRef, {
    bars,
    overlays: effectiveOverlays,
    hidden,
    effectiveThemeRef,
    rebuildToken: candleType,
  })
  // Bollinger Bands three-line reconcile (Plan 0082 phase 2). Static colour, so no
  // theme read — draws upper/middle/lower on the price pane.
  useBbandsSeries(chartRef, bbandsSeriesRef, {
    bars,
    overlays: effectiveOverlays,
    hidden,
    rebuildToken: candleType,
  })
  // Ichimoku five-line + displaced filled cloud primitive (Plan 0073 phase 4).
  // Feeds the primitive attached in the creation effect; reserves right-edge space
  // so the projected cloud shows past the last candle.
  useIchimokuSeries(chartRef, containerRef, ichimokuPrimitiveRef, {
    bars,
    overlays: effectiveOverlays,
    hidden,
    effectiveTheme,
    rebuildToken: candleType,
  })
  // Oscillator panes a divergence needs (Plan 0091 phase 9): ensured below even if
  // the user hasn't added — or has toggled off — that oscillator, so the divergence's
  // oscillator segment always has a pane. `obv` uses the always-on OBV base pane.
  const requiredOscillatorKinds = useMemo(
    () => requiredOscillatorKindsFor(divergences),
    [divergences],
  )
  // Oscillator sub-panes (Plan 0091 phase 6): each active oscillator overlay draws
  // in its own real v5 pane (via the shared PaneRegistry), toggleable from the
  // layers legend. Reconciles create / reuse / teardown by stable pane id.
  useOscillatorPanes(chartRef, paneRegistryRef, oscillatorPanesRef, {
    bars,
    overlays: effectiveOverlays,
    hidden,
    requiredKinds: requiredOscillatorKinds,
    rebuildToken: candleType,
    syncTestRenderHook,
  })
  // Divergence segments (Plan 0091 phase 9, ADR-0090): feed the price/OBV/oscillator
  // divergence primitives their segments + theme colours. Runs after the pane
  // reconcile so every oscillator pane's primitive exists.
  useDivergences(
    containerRef,
    divergencePricePrimitiveRef,
    obvDivergencePrimitiveRef,
    oscillatorPanesRef,
    { divergences, effectiveTheme, rebuildToken: candleType },
  )

  // Live forming-bar update (Plan 0049 phase 10): feed the already-polled `/quote`
  // into the chart's CURRENT (forming) bar in place (Plan 0072 phase 8: `useFormingBar`).
  useFormingBar(seriesRef, { quote, bars, timeframe, candleType })

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

  // Candlestick markers + pattern-span band (Plan 0049 phases 7 & 10 / Plan 0071
  // phase 2): draw only the enabled groups' markers + spans, themed, with the
  // clicked-bar affordance and hover emphasis (Plan 0072 phase 8: `useChartMarkers`).
  useChartMarkers(seriesRef, containerRef, spanPrimitiveRef, {
    drawnMarkers,
    clickedBarTs,
    highlightedCandleGroup,
    effectiveTheme,
    styleVersion,
    rebuildToken: candleType,
  })

  // Price lines (Plan 0047 phase 9): reconcile horizontal `price_line` overlays
  // (S/R levels the agent pushes) on the main series (Plan 0072 phase 8:
  // `usePriceLines`).
  usePriceLines(seriesRef, containerRef, priceLinesRef, {
    overlays: effectiveOverlays,
    hidden,
    effectiveTheme,
    styleVersion,
    rebuildToken: candleType,
  })

  // Build the layers-legend descriptor list (Plan 0047 phase 9 / Plan 0067 ph3 /
  // Plan 0071 ph2): one row per overlay, a candlestick master + per-group rows,
  // per price line, and per trendline group (Plan 0072 phase 8: `useLayersLegend`
  // owns the state via the pure `buildChartLayers`).
  const layers = useLayersLegend(containerRef, {
    overlays: effectiveOverlays,
    candleGroups,
    enabledCandleGroups,
    visibleTrendlines,
    hidden,
    hasObv: bars.length > 0,
    userOverlayKeys: merged.userKeys,
    effectiveTheme,
    styleVersion,
  })

  // Hover tooltip (Plan 0047 phase 8 / Plan 0067 phase 2): crosshair-driven
  // marker/overlay/trendline read-out + pattern-bar outline (Plan 0072 phase 8:
  // `useChartTooltip` owns the state and returns it).
  const tooltip = useChartTooltip(
    chartRef,
    overlaySeriesRef,
    spanPrimitiveRef,
    trendlinePrimitiveRef,
    divergencePricePrimitiveRef,
    { drawnMarkers, rebuildToken: candleType },
  )

  // Re-apply the EXISTING chart's colours + line widths on a theme flip or a
  // chart-style store mutation (Plan 0068 phase 2) — in place via `applyOptions`,
  // no remount (Plan 0072 phase 8: `useChartRestyle`).
  useChartRestyle(
    {
      containerRef,
      chartRef,
      seriesRef,
      volumeSeriesRef,
      volumeMaSeriesRef,
      vwapSeriesRef,
      obvSeriesRef,
      overlaySeriesRef,
      supertrendSeriesRef,
    },
    { effectiveTheme, styleVersion, candleType },
  )

  // Track the effective theme; the subscription fires on an explicit theme
  // change and on an OS flip while in `system` mode. Unsubscribes on unmount.
  useEffect(() => subscribeEffective(setEffectiveTheme), [])

  // Re-resolve + re-apply on any chart-style store mutation (colour, width, or
  // candle-type) by bumping the version the restyle effect keys on. Unsubscribes
  // on unmount.
  useEffect(() => subscribeChartStyle(() => setStyleVersion((v) => v + 1)), [])

  return (
    <div className={styles.wrapper}>
      <ChartToolbar
        agentModeEnabled={agentModeEnabled}
        selectRangeMode={selectRangeMode}
        toggleSelectRange={toggleSelectRange}
        scanStatus={scanStatus}
        chartScanStatus={chartScanStatus}
        onScanPatterns={scanVisibleRange}
        onScanChartPatterns={scanChartPatternsVisibleRange}
        symbol={symbol}
        timeframe={timeframe}
      />
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
          onToggle={onLayerToggle}
          onHighlight={onLayerHighlight}
          onAddOverlay={canAddOverlay ? handleAddOverlay : undefined}
          onRemove={handleRemoveOverlay}
        />
      </div>
    </div>
  )
}
