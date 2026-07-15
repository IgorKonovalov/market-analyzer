/**
 * CandlestickChart — the thin React adapter over the imperative chart core
 * (Plan 0098 / ADR-0092). All lightweight-charts wiring — the chart instance, the
 * main + always-on series, the panes, the five main-series primitives, the overlay
 * and oscillator-pane reconcilers, restyle, the axis and the forming bar — lives in a
 * plain-TS `ChartController` (`lib/chart/`). This component builds the controller
 * once, drives it through declarative forward effects (mount / setBars / setOverlays /
 * setPriceLines / setOscillators / setTrendlines / setIchimoku / setDivergences /
 * setMarkers / restyle / setTimeframeAxis / setQuote), and keeps only what genuinely
 * produces React state + JSX: the gesture / tooltip / scan / lazy-history / legend /
 * candle-marker-group hooks, the user-overlay + layer-visibility + preset stores, and
 * the render tree.
 *
 * A handful of reconcilers added AFTER this plan was drafted — Plan 0092's fib/pivot
 * price lines + anchored VWAP + market-structure markers, Plan 0105's lazy OBV pane —
 * are still fed through their own hooks (useStructureLevels / useAnchoredVwapSeries /
 * useMarketStructureMarkers / useObvPane), reading the controller's chart / series /
 * pane handles. The remaining `lightweight-charts` type imports here belong to those;
 * folding them into the controller is the documented followup.
 *
 * Disposing on unmount is non-negotiable — without it every navigation leaks a
 * Canvas/WebGL context (`controller.dispose()`). See
 * ui-builder/references/best-practices.md.
 *
 * The renderer exposes `window.__test_chart_render__` reflecting what's actually drawn
 * (one entry per series, including the candlestick). Playwright `live-chart.spec.ts`
 * and the renderer-side specs assert against that — NOT the reducer's overlay list —
 * so a render regression that loses a series cannot pass.
 */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import type { ISeriesApi } from 'lightweight-charts'

import type { IPriceLine } from 'lightweight-charts'

import { t } from '../lib/i18n'
import { useChartGestures } from '../hooks/useChartGestures'
import { useChartPatternRecompute } from '../hooks/useChartPatternRecompute'
import { useChartScans } from '../hooks/useChartScans'
import { useChartTooltip } from '../hooks/useChartTooltip'
import { useCandleMarkerGroups } from '../hooks/useCandleMarkerGroups'
import { useLayersLegend } from '../hooks/useLayersLegend'
import { useLazyHistoryTrigger } from '../hooks/useLazyHistoryTrigger'
import { useObvPane } from '../hooks/useObvPane'
import { useAnchoredVwapSeries } from '../hooks/useAnchoredVwapSeries'
import { useMarketStructureMarkers } from '../hooks/useMarketStructureMarkers'
import { useStructureLevels } from '../hooks/useStructureLevels'
import type { ChartMarker } from '../lib/markers'
import { candleGroupKeyFromLayerId } from '../lib/candleGroups'
import { ChartLegend } from './ChartLegend'
import { ChartSidePanel } from './ChartSidePanel'
import { ChartToolbar } from './ChartToolbar'
import { ChartTooltip } from './ChartTooltip'
import { MarketStructureBadge } from './MarketStructureBadge'
import { buildLegendValues } from '../lib/legendValues'
import { marketStructure } from '../lib/marketStructure'
import { MARKET_STRUCTURE_LAYER_ID, mainSeriesKind } from '../lib/chartSeries'
import { formatRangeLabel } from '../lib/chartAxis'
import { dedupeTrendlines, patternStateKey, trendlineGroupLayerId } from '../lib/trendlines'
import { requiredOscillatorKindsFor, type DivergencePrimitive } from '../lib/divergences'
import { useDrawingTools } from '../hooks/useDrawingTools'
import { ChartController } from '../lib/chart/controller'
import { routeLayerHighlight, routeLayerToggle } from '../lib/chart/legendRouting'
import { DrawingRail } from './DrawingRail'
import {
  getStoredTheme,
  resolveEffective,
  subscribeEffective,
  type EffectiveTheme,
} from '../lib/theme'
import { getCandleType, subscribeChartStyle } from '../lib/chartStyle'
import {
  addUserOverlay,
  getUserOverlaysSnapshot,
  mergeOverlays,
  removeUserOverlay,
  setUserOverlays,
  subscribeUserOverlays,
  userOverlayStoreKey,
} from '../lib/userOverlays'
import {
  getLayerVisibilitySnapshot,
  hiddenForBucket,
  layerVisibilityStoreKey,
  setLayerVisibility,
  subscribeLayerVisibility,
  toggleLayerVisibility,
} from '../lib/layerVisibility'
import {
  CLEAN_PRESET_NAME,
  allPresets,
  getUserPresetsSnapshot,
  hiddenForPreset,
  saveCurrentAsPreset,
  subscribeChartPresets,
  type ChartPreset,
  type PresetShow,
} from '../lib/chartPresets'
import { overlayLayerId } from '../lib/overlays'
import type { Bar } from '../types/sidecar/bar'
import type {
  Divergence,
  DrawingKind,
  DrawingSpec,
  OverlaySpec,
  TrendlineSpec,
} from '../types/events'
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

// Stable empty agent-drawing list (Plan 0097 phase 4) — same re-render stability.
const NO_AGENT_DRAWINGS: ReadonlyArray<DrawingSpec> = []

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
  /** Plan 0097 phase 4 (ADR-0091): agent-placed freeform drawings from
   * `chart.annotations`, merged with the user's local drawings by `useDrawingTools`
   * (agent = hide-only, user = editable). */
  agentDrawings?: ReadonlyArray<DrawingSpec>
  ariaLabel?: string
  /** Carried in the gesture payloads so the agent knows which chart fired
   * (Plan 0014; gestures forward unconditionally per ADR-0101). */
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
  agentDrawings = NO_AGENT_DRAWINGS,
  ariaLabel,
  symbol,
  timeframe,
  quote,
  onReachLeftEdge,
  historyTriggerEnabled = false,
}: Props): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)
  // The imperative lightweight-charts core (Plan 0098 / ADR-0092): owns the chart
  // instance, the main + always-on series, the PaneRegistry, and the five main-
  // series primitives, behind a declarative API. Instantiated once and reused
  // across candle-type rebuilds (dispose → mount on the same instance); its
  // ref-object handles are stable identities the still-React hooks below capture,
  // exactly as the former component refs were. The remaining refs here belong to
  // reconciler concerns (overlays, oscillator panes, OBV) that later phases fold in.
  const controllerRef = useRef<ChartController | null>(null)
  if (controllerRef.current === null) controllerRef.current = new ChartController()
  const controller = controllerRef.current
  // Price-structure horizontal lines (Plan 0092 phase 5): `fibonacci` grid ratios
  // + classic `pivot_points` P/R/S, drawn as price lines on the main series (a
  // separate map from the controller's `price_line` overlays so the two families
  // never collide). Fed by `useStructureLevels`.
  const structureLinesRef = useRef<Map<string, IPriceLine>>(new Map())
  // Anchored-VWAP overlays (Plan 0092 phase 5) draw one line series each on the
  // price pane, keyed by overlayKey; a legend toggle removes it. Fed by
  // `useAnchoredVwapSeries`.
  const anchoredVwapSeriesRef = useRef<Map<string, ISeriesApi<'Line'>>>(new Map())
  // OBV-pane divergence primitive (Plan 0091 phase 9, ADR-0090): rides the OBV line
  // series (draws obv oscillator-pivot segments), attached/detached with the OBV pane
  // by `useObvPane`. The price-pane divergence primitive lives in the controller
  // (attached to the main series at creation); each oscillator pane's own primitive is
  // attached by the controller's oscillator reconciler. All fed by `useDivergences`.
  const obvDivergencePrimitiveRef = useRef<DivergencePrimitive | null>(null)
  // OBV line series (Plan 0105 phase 3): lazily created/removed by `useObvPane` with
  // its own sub-pane, so it is not one of the controller's always-on series.
  const obvSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
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

  // Price-action market structure (Plan 0092 phase 6, ADR-0084): computed client-
  // side from the bars the chart holds (the same posture as the fib/pivot overlays),
  // feeding the HH/HL/LH/LL + BOS/CHoCH markers and the structural-trend badge. A
  // second, distinct trend read — reported beside the price, never merged into any
  // indicator trend.
  const marketStructureResult = useMemo(() => marketStructure(bars), [bars])
  // Whether the bars carry confirmed structure — gates the toggleable legend row,
  // the on-chart markers, and the badge (all off by default, Plan 0096 phase 3).
  // `hidden` is resolved below; the badge gates on it inline at render.
  const hasMarketStructure =
    marketStructureResult.labeledPivots.length > 0 || marketStructureResult.events.length > 0
  // Add / remove a user overlay (Plan 0082 phase 4). Only available when the chart
  // carries a (symbol, timeframe) to key the store by. Remove maps the legend row
  // id back to the stored spec via its overlayLayerId.
  const canAddOverlay = Boolean(symbol && timeframe)

  // Persisted per-(symbol,timeframe) layer visibility (Plan 0096 phase 3,
  // ADR-0089): the formerly-ephemeral `hidden` set is promoted to renderer-owned
  // display state. The in-memory shape stays a ReadonlySet<string>, so every
  // consumer (useCandleMarkerGroups, buildChartLayers, the series-visibility
  // effects) is unaffected. A symbol-less chart (no bucket to key) keeps the
  // legacy ephemeral, all-visible behaviour.
  const visibilitySnapshot = useSyncExternalStore(
    subscribeLayerVisibility,
    getLayerVisibilitySnapshot,
    getLayerVisibilitySnapshot,
  )
  const bucketKey = symbol && timeframe ? layerVisibilityStoreKey(symbol, timeframe) : null
  const [ephemeralHidden, setEphemeralHidden] = useState<ReadonlySet<string>>(() => new Set())
  const hidden = useMemo<ReadonlySet<string>>(() => {
    if (bucketKey === null || !symbol || !timeframe) return ephemeralHidden
    return hiddenForBucket(visibilitySnapshot, symbol, timeframe)
  }, [bucketKey, ephemeralHidden, visibilitySnapshot, symbol, timeframe])

  // The applied-preset name (Plan 0096 phase 3): the legend selector shows it
  // until the layout diverges, then reads "Custom". A fresh bucket (no stored
  // visibility) opens on Clean; any user tweak clears it to Custom. Re-derived
  // when the (symbol, timeframe) changes so a symbol switch reflects that
  // bucket's provenance (reading the store directly, not the render snapshot,
  // keeps this keyed on bucketKey alone).
  const [activePreset, setActivePreset] = useState<string | null>(null)
  useEffect(() => {
    if (bucketKey === null) {
      setActivePreset(null)
      return
    }
    setActivePreset(
      getLayerVisibilitySnapshot()[bucketKey] === undefined ? CLEAN_PRESET_NAME : null,
    )
  }, [bucketKey])

  const handleAddOverlay = useCallback(
    (spec: OverlaySpec): void => {
      if (symbol && timeframe) addUserOverlay(symbol, timeframe, spec)
      setActivePreset(null)
    },
    [symbol, timeframe],
  )
  const handleRemoveOverlay = useCallback(
    (id: string): void => {
      if (!symbol || !timeframe) return
      const spec = userOverlays.find((s) => overlayLayerId(s) === id)
      if (spec) removeUserOverlay(symbol, timeframe, spec)
      setActivePreset(null)
    },
    [symbol, timeframe, userOverlays],
  )
  // Toggle a layer's visibility (Plan 0096 phase 3): write through the persisted
  // store when the chart is keyed, else the ephemeral fallback. Any manual toggle
  // diverges from an applied preset → "Custom".
  const toggleLayer = useCallback(
    (id: string): void => {
      if (bucketKey === null || !symbol || !timeframe) {
        setEphemeralHidden((prev) => {
          const next = new Set(prev)
          if (next.has(id)) next.delete(id)
          else next.add(id)
          return next
        })
      } else {
        toggleLayerVisibility(symbol, timeframe, id)
      }
      setActivePreset(null)
    },
    [bucketKey, symbol, timeframe],
  )
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
  // Dispatch the two-legend routing decision (pure `legendRouting`): a candlestick
  // GROUP row toggles the enabled set, everything else toggles `hidden`; a candle
  // group key drives marker emphasis, any other key the trendline primitive.
  const onLayerToggle = useCallback(
    (id: string): void => {
      const route = routeLayerToggle(id)
      if (route.kind === 'candleGroup') toggleCandleGroup(route.groupKey)
      else toggleLayer(route.id)
    },
    [toggleCandleGroup, toggleLayer],
  )
  const onLayerHighlight = useCallback(
    (key: string | null): void => {
      const route = routeLayerHighlight(key, candleKeySet)
      if (route.kind === 'candleGroup') {
        setHighlightedCandleGroup(route.key)
        setHighlightedTrendlineKey(null)
      } else {
        setHighlightedTrendlineKey(route.key)
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
  } = useChartScans(controller.chartRef, { symbol, timeframe })

  // Reflect what's drawn into the test hook. Stable identity (reads only the stable
  // controller + refs), so it can sit in the effect dep arrays without retriggering
  // them. The controller owns the main + always-on series; OBV and the agent
  // overlays are still reconciled by their hooks, so they're read from their refs.
  const syncTestRenderHook = useCallback((): void => {
    const kinds: Array<{ kind: string; period?: number | null }> = []
    if (controller.seriesRef.current !== null) {
      // Read the type from the store (not a dep) so this stays a stable callback;
      // a candle-type change rebuilds via the creation effect, which calls this.
      kinds.push({ kind: mainSeriesKind(getCandleType()) })
    }
    // Always-on volume series, between the candlestick and the agent overlays.
    if (controller.volumeSeriesRef.current !== null) kinds.push({ kind: 'volume' })
    if (controller.volumeMaSeriesRef.current !== null) kinds.push({ kind: 'volume_ma' })
    if (controller.vwapSeriesRef.current !== null) kinds.push({ kind: 'vwap' })
    if (obvSeriesRef.current !== null) kinds.push({ kind: 'obv' })
    for (const { spec } of controller.overlaySeriesRef.current.values()) {
      kinds.push({ kind: spec.kind, period: spec.period ?? null })
    }
    window.__test_chart_render__ = {
      seriesCount: kinds.length,
      seriesKinds: kinds,
      barCount: controller.barCount,
    }
  }, [controller])

  // Create the chart on mount, dispose on unmount, rebuild on a candle-type change
  // (the series type is fixed at creation, so `candleType` keys this effect). The
  // controller owns the imperative wiring (chart, series, panes, primitives); this
  // effect is the React lifecycle shell that drives it and clears the still-external
  // reconciler bookkeeping the chart's own `remove()` already disposed, so their
  // hooks rebuild on the fresh chart (Plan 0098 phase 1; later phases fold these
  // reconcilers into the controller too). The theme ref gives the current theme
  // without making this effect re-run — a flip recolours in place (restyle effect).
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    controller.mount(container, { candleType, theme: effectiveThemeRef.current })

    // Capture the still-external Map references into locals for the cleanup closure
    // (react-hooks/exhaustive-deps: ref.current may change between effect run and
    // cleanup invocation; the local capture is the canonical fix). The overlay,
    // supertrend, bbands, price-line and oscillator-pane maps now live in the
    // controller and are cleared by `controller.dispose()`.
    const structureLineMap = structureLinesRef.current
    const anchoredVwapMap = anchoredVwapSeriesRef.current
    syncTestRenderHook()

    return () => {
      controller.dispose()
      // `controller.dispose()` (via chart.remove) disposes the panes + their series
      // and clears its own reconciler bookkeeping; drop the still-external maps so
      // their hooks rebuild on the fresh chart.
      obvSeriesRef.current = null
      obvDivergencePrimitiveRef.current = null
      structureLineMap.clear()
      anchoredVwapMap.clear()
      syncTestRenderHook()
    }
    // `candleType` rebuilds the chart (series type is fixed at creation); the data
    // / marker / primitive effects + chart-subscribing hooks key on it too, so they
    // re-run and re-attach to the fresh series in the same commit (Plan 0068 ph4).
  }, [controller, syncTestRenderHook, candleType])

  // Push bar data through the controller: it fills the main + always-on series and
  // handles the scroll-anchored left-edge prepend (Plan 0030) vs fit-on-genuine-
  // -change (Plan 0049 ph11) internally. `candleType` re-runs this after a rebuild
  // so the fresh main series gets its data (Plan 0068 ph4); overlay/supertrend
  // reconcile lives in its own hook (Plan 0072 phase 8), so this doesn't key on
  // overlays/hidden.
  useEffect(() => {
    controller.setBars(bars)
    syncTestRenderHook()
  }, [controller, bars, syncTestRenderHook, candleType])

  // Price-pane overlay reconcile — ema/sma line series + the supertrend pair + the
  // bbands triple — folded into the controller (Plan 0098 phase 2, ADR-0092). Keyed
  // on bars/overlays/hidden (candleType is the rebuild token), NOT the theme: a
  // created series takes the theme's colour, but existing series recolour in place
  // via the restyle path, so a flip must not re-run this. The theme is read live off
  // the ref. Runs after the bars effect so the main series has data on each commit.
  useEffect(() => {
    controller.setOverlays({
      bars,
      overlays: effectiveOverlays,
      hidden,
      theme: effectiveThemeRef.current,
    })
    syncTestRenderHook()
  }, [controller, bars, effectiveOverlays, hidden, candleType, syncTestRenderHook])
  // Ichimoku five-line + displaced filled cloud primitive (Plan 0073 phase 4) —
  // folded into the controller (Plan 0098 phase 3). Feeds the primitive attached at
  // creation + reserves right-edge space for the projected cloud. `effectiveTheme`
  // re-reads the colour tokens on a flip.
  useEffect(() => {
    controller.setIchimoku({ bars, overlays: effectiveOverlays, hidden })
  }, [controller, bars, effectiveOverlays, hidden, effectiveTheme, candleType])
  // OBV pane lifecycle (Plan 0105 phase 3): lazy create/remove like the oscillator
  // panes — toggling OBV off removes its pane (no empty ~30px band; a Clean chart
  // is born without one), toggling on re-creates it as the FIRST sub-pane with its
  // divergence primitive re-attached. An obv divergence keeps the pane (series
  // hidden) so its oscillator segment always has a pane to draw on. Runs before
  // `useOscillatorPanes`/`useDivergences` so the pane + primitive exist first.
  useObvPane(
    controller.chartRef,
    containerRef,
    controller.paneRegistryRef,
    obvSeriesRef,
    obvDivergencePrimitiveRef,
    {
      bars,
      hidden,
      divergences,
      effectiveThemeRef,
      rebuildToken: candleType,
      syncTestRenderHook,
    },
  )
  // Oscillator panes a divergence needs (Plan 0091 phase 9): ensured below even if
  // the user hasn't added — or has toggled off — that oscillator, so the divergence's
  // oscillator segment always has a pane. `obv` uses the OBV pane `useObvPane` owns.
  const requiredOscillatorKinds = useMemo(
    () => requiredOscillatorKindsFor(divergences),
    [divergences],
  )
  // Oscillator sub-panes (Plan 0091 phase 6): each active oscillator overlay draws
  // in its own real v5 pane (via the shared PaneRegistry), toggleable from the
  // layers legend — reconcile create / reuse / teardown by stable pane id, folded
  // into the controller (Plan 0098 phase 2). Runs after `useObvPane` (which claims
  // pane slot 0) so oscillators take the slots below it.
  useEffect(() => {
    controller.setOscillators({
      bars,
      overlays: effectiveOverlays,
      hidden,
      requiredKinds: requiredOscillatorKinds,
    })
    syncTestRenderHook()
  }, [
    controller,
    bars,
    effectiveOverlays,
    hidden,
    requiredOscillatorKinds,
    candleType,
    syncTestRenderHook,
  ])
  // Divergence segments (Plan 0091 phase 9, ADR-0090): feed the price/OBV/oscillator
  // divergence primitives their segments + colours — folded into the controller
  // (Plan 0098 phase 3). The OBV divergence primitive is still owned by `useObvPane`
  // and passed in. Runs after the pane reconcile so every oscillator pane exists.
  useEffect(() => {
    controller.setDivergences(divergences, obvDivergencePrimitiveRef.current)
  }, [controller, divergences, candleType])

  // Live forming-bar update (Plan 0049 phase 10) — folded into the controller
  // (Plan 0098 phase 3).
  useEffect(() => {
    controller.setQuote(quote, bars, timeframe)
  }, [controller, quote, bars, timeframe, candleType])

  // Monthly axis ticks (Plan 0050 phase 7): the `1mo` timeframe needs month/year
  // tick marks — folded into the controller (Plan 0098 phase 3). Re-applied on a
  // rebuild (the fresh chart needs the formatter).
  useEffect(() => {
    controller.setTimeframeAxis(timeframe)
  }, [controller, timeframe, candleType])

  // Freeform-drawing tool mode (Plan 0097 phase 2, ADR-0091). The component owns
  // `activeTool` so it can coordinate the two pointer machines: `useChartGestures`
  // parks while a tool is armed (`suspended`), and the drawing machine parks (no
  // active tool) while range-select is on.
  const [activeTool, setActiveTool] = useState<DrawingKind | null>(null)

  // Pointer-gesture state machine + agent-mode POSTs (Plan 0029 phase 1).
  // Called AFTER the chart-creation effect so its gesture effect sees a
  // populated `chartRef`/`seriesRef` on mount.
  const { selectRangeMode, toggleSelectRange, selection, rangeLabel, clickedBarTs } =
    useChartGestures(containerRef, controller.chartRef, controller.seriesRef, {
      symbol,
      timeframe,
      bars,
      suspended: activeTool !== null,
    })

  // Drawing tool machine + edit engine (Plan 0097 phase 2). Feeds the drawing
  // primitive; called after the chart-creation effect so the refs are populated.
  const {
    setActiveTool: setDrawingTool,
    selectedId: selectedDrawingId,
    selectedProvenance: selectedDrawingProvenance,
    deleteSelected: deleteSelectedDrawing,
  } = useDrawingTools(
    containerRef,
    controller.chartRef,
    controller.seriesRef,
    controller.drawingPrimitiveRef,
    {
      symbol,
      bars,
      selectRangeMode,
      activeTool,
      onActiveToolChange: setActiveTool,
      agentDrawings,
      rebuildToken: candleType,
    },
  )

  // Keep the two pointer machines mutually exclusive: arming a drawing tool exits
  // range-select, and entering range-select disarms the drawing tool.
  const handleSelectTool = useCallback(
    (tool: DrawingKind | null): void => {
      if (tool !== null && selectRangeMode) toggleSelectRange()
      setDrawingTool(tool)
    },
    [selectRangeMode, toggleSelectRange, setDrawingTool],
  )
  const handleToggleSelectRange = useCallback((): void => {
    if (!selectRangeMode) setDrawingTool(null)
    toggleSelectRange()
  }, [selectRangeMode, toggleSelectRange, setDrawingTool])

  // Lazy backward paging (Plan 0030): ask the parent for older bars when the
  // user scrolls near the left edge. A sibling concern to the pointer gestures
  // (it is not a pointer gesture), and likewise called after the chart-creation
  // effect so `chartRef` is populated on mount.
  useLazyHistoryTrigger(controller.chartRef, {
    enabled: historyTriggerEnabled && onReachLeftEdge !== undefined,
    onReachLeftEdge: onReachLeftEdge ?? NOOP,
    rebuildToken: candleType,
  })

  // Recompute chart-pattern trendlines on mount + debounced visible-range settle
  // (Plan 0064 phase 5, ADR-0059) so the lines are re-derived for the bars on
  // screen and return after a reload. Called after the chart-creation effect so
  // `chartRef` is populated on mount; gated off until symbol+timeframe are known.
  useChartPatternRecompute(controller.chartRef, {
    enabled: symbol !== undefined && timeframe !== undefined,
    onRecompute: () => {
      void recomputeTrendlines()
    },
    rebuildToken: candleType,
  })

  // Trendline overlay primitive (Plan 0052 phase 4, ADR-0049): feed the primitive
  // (attached at creation) its specs/colours/visibility — folded into the controller
  // (Plan 0098 phase 3). `effectiveTheme` re-reads the colour tokens on a flip.
  useEffect(() => {
    controller.setTrendlines(shownTrendlines, highlightedTrendlineKey)
  }, [controller, shownTrendlines, highlightedTrendlineKey, effectiveTheme, candleType])

  // Market-structure markers (Plan 0092 phase 6, ADR-0084): HH/HL/LH/LL labels at
  // the confirmed swing pivots + BOS/CHoCH glyphs at their events, on their own
  // markers plugin. Called before `useChartMarkers` so the candlestick-pattern
  // markers own the last write to the shared series-markers capture. Returns the
  // drawn points for the tooltip's structure hover (Plan 0105 phase 7).
  const structureMarkerPoints = useMarketStructureMarkers(controller.seriesRef, containerRef, {
    structure: marketStructureResult,
    bars,
    hidden,
    effectiveTheme,
    styleVersion,
    rebuildToken: candleType,
  })

  // Candlestick markers + pattern-span band (Plan 0049 phases 7 & 10 / Plan 0071
  // phase 2): draw only the enabled groups' markers + spans, themed, with the
  // clicked-bar affordance and hover emphasis — folded into the controller (Plan
  // 0098 phase 3). Runs after `useMarketStructureMarkers` so the candlestick markers
  // own the last write to the shared series-markers capture.
  useEffect(() => {
    controller.setMarkers({
      drawnMarkers,
      clickedBarTs,
      highlightGroup: highlightedCandleGroup,
      theme: effectiveTheme,
    })
  }, [
    controller,
    drawnMarkers,
    clickedBarTs,
    highlightedCandleGroup,
    effectiveTheme,
    styleVersion,
    candleType,
  ])

  // Price lines (Plan 0047 phase 9): reconcile horizontal `price_line` overlays
  // (S/R levels the agent pushes) on the main series — folded into the controller
  // (Plan 0098 phase 2). Recolours in place, so this DOES key on the theme +
  // styleVersion (candleType is the rebuild token).
  useEffect(() => {
    controller.setPriceLines({ overlays: effectiveOverlays, hidden, theme: effectiveTheme })
  }, [controller, effectiveOverlays, hidden, effectiveTheme, styleVersion, candleType])

  // Price-structure horizontal lines (Plan 0092 phase 5): `fibonacci` grid ratios
  // + classic `pivot_points` P/R/S drawn as price lines on the main series
  // (client-computed from bars, auto-anchored or from the overlay's explicit
  // anchor/method). Toggled per overlay from the legend like the other overlays.
  // Returns the drawn levels for the nearest-level-on-hover tooltip lookup
  // (Plan 0105 phase 6).
  const structureLevels = useStructureLevels(controller.seriesRef, structureLinesRef, {
    bars,
    overlays: effectiveOverlays,
    hidden,
    rebuildToken: candleType,
  })
  // Anchored-VWAP line series (Plan 0092 phase 5): one line per `anchored_vwap`
  // overlay, accumulated from its anchor (explicit or dominant-swing auto-anchor).
  useAnchoredVwapSeries(controller.chartRef, anchoredVwapSeriesRef, {
    bars,
    overlays: effectiveOverlays,
    hidden,
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
    hasMarketStructure,
    userOverlayKeys: merged.userKeys,
    effectiveTheme,
    styleVersion,
  })

  // Live last-bar values for the inline legend (Plan 0096 phase 2): each
  // indicator overlay + the OBV strip, computed client-side from the same bars
  // the chart draws with. Pure + memoised on the bars/overlays that feed it.
  const legendValues = useMemo(
    () => buildLegendValues(bars, effectiveOverlays, bars.length > 0),
    [bars, effectiveOverlays],
  )

  // Chart presets (Plan 0096 phase 3, ADR-0089): the built-ins plus any user-saved
  // presets, offered in the legend's selector. Applying is "not pinned" — it
  // writes the preset's overlays into the user bucket and the resolved hidden set
  // into the visibility bucket for the current (symbol, timeframe), then normal
  // stickiness remembers any tweak. Presets never touch `candleType`.
  const userPresetsSnapshot = useSyncExternalStore(
    subscribeChartPresets,
    getUserPresetsSnapshot,
    getUserPresetsSnapshot,
  )
  const presets = useMemo(() => allPresets(userPresetsSnapshot), [userPresetsSnapshot])
  const applyPreset = useCallback(
    (preset: ChartPreset): void => {
      if (!symbol || !timeframe) return
      setUserOverlays(symbol, timeframe, preset.overlays)
      setLayerVisibility(symbol, timeframe, hiddenForPreset(preset, layers))
      setActivePreset(preset.name)
    },
    [symbol, timeframe, layers],
  )
  // Capture the current category visibility for "save current as preset". Overlay
  // membership comes from the user's own overlays; the category flags read the
  // live resolved layers.
  const handleSavePreset = useCallback(
    (name: string): void => {
      const show: PresetShow = {
        obv: layers.some((l) => l.kind === 'series' && l.visible),
        candlesticks: layers.some((l) => l.kind === 'marker' && l.visible),
        trendlines: layers.some((l) => l.kind === 'trendline' && l.visible),
        priceLines: layers.some((l) => l.kind === 'price_line' && l.visible),
      }
      saveCurrentAsPreset(name, userOverlays, show)
      setActivePreset(name)
    },
    [layers, userOverlays],
  )

  // Quick toggle-all (user request 2026-07-14, post-0105): one click hides every
  // hidden-set-governed layer — candlestick DETAIL rows ride their master, which
  // is included — the next click restores the mix that was showing before. The
  // stash is per-bucket and in-memory only; without one (e.g. after a remount)
  // "show" falls back to everything-visible. A bulk toggle diverges from an
  // applied preset → Custom, like any manual toggle.
  const preToggleAllRef = useRef<{ bucket: string | null; hidden: ReadonlySet<string> } | null>(
    null,
  )
  const hiddenGovernedLayers = useMemo(
    () => layers.filter((l) => candleGroupKeyFromLayerId(l.id) === null),
    [layers],
  )
  const allHidden = hiddenGovernedLayers.length > 0 && hiddenGovernedLayers.every((l) => !l.visible)
  const handleToggleAll = useCallback((): void => {
    const write = (next: ReadonlySet<string>): void => {
      if (bucketKey === null || !symbol || !timeframe) setEphemeralHidden(new Set(next))
      else setLayerVisibility(symbol, timeframe, next)
    }
    if (allHidden) {
      const stash = preToggleAllRef.current
      write(stash !== null && stash.bucket === bucketKey ? stash.hidden : new Set())
      preToggleAllRef.current = null
    } else {
      preToggleAllRef.current = { bucket: bucketKey, hidden }
      write(new Set([...hidden, ...hiddenGovernedLayers.map((l) => l.id)]))
    }
    setActivePreset(null)
  }, [allHidden, bucketKey, symbol, timeframe, hidden, hiddenGovernedLayers])

  // Hover tooltip (Plan 0047 phase 8 / Plan 0067 phase 2): crosshair-driven
  // marker/overlay/trendline read-out + pattern-bar outline (Plan 0072 phase 8:
  // `useChartTooltip` owns the state and returns it).
  const tooltip = useChartTooltip(
    controller.chartRef,
    controller.overlaySeriesRef,
    controller.spanPrimitiveRef,
    controller.trendlinePrimitiveRef,
    controller.divergencePricePrimitiveRef,
    controller.drawingPrimitiveRef,
    {
      drawnMarkers,
      structureLevels,
      seriesRef: controller.seriesRef,
      structureMarkers: structureMarkerPoints,
      rebuildToken: candleType,
    },
  )

  // Re-apply the EXISTING chart's colours + line widths on a theme flip or a
  // chart-style store mutation (Plan 0068 phase 2) — in place via `applyOptions`, no
  // remount, folded into the controller (Plan 0098 phase 3). The OBV series is still
  // owned by `useObvPane` and passed in.
  useEffect(() => {
    controller.restyle(effectiveTheme, obvSeriesRef.current)
  }, [controller, effectiveTheme, styleVersion, candleType])

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
        selectRangeMode={selectRangeMode}
        toggleSelectRange={handleToggleSelectRange}
        scanStatus={scanStatus}
        chartScanStatus={chartScanStatus}
        onScanPatterns={scanVisibleRange}
        onScanChartPatterns={scanChartPatternsVisibleRange}
        symbol={symbol}
        timeframe={timeframe}
      />
      <div className={styles.chartArea}>
        {/* Left-edge drawing dock (Plan 0097 phase 2, ADR-0091), filling the slot
            Plan 0096 reserved. The rail arms tools; the drawing layer + edit
            engine live on the chart via `useDrawingTools`. */}
        <div className={styles.leftRail} data-testid="chart-left-rail">
          <DrawingRail
            activeTool={activeTool}
            onSelectTool={handleSelectTool}
            onDelete={deleteSelectedDrawing}
            hasSelection={selectedDrawingId !== null}
            selectedProvenance={selectedDrawingProvenance}
            disabled={symbol === undefined}
          />
        </div>
        <div
          ref={containerRef}
          className={`${styles.chartContainer} ${selectRangeMode ? styles.selectRangeActive : ''}`.trim()}
          data-testid="candlestick-chart"
          role="img"
          aria-label={ariaLabel ?? t('chart.ariaLabel', { count: bars.length })}
        />
        {!hidden.has(MARKET_STRUCTURE_LAYER_ID) && (
          <MarketStructureBadge structure={marketStructureResult} />
        )}
        <ChartLegend
          layers={layers}
          values={legendValues}
          onToggle={onLayerToggle}
          onHighlight={onLayerHighlight}
          onAddOverlay={canAddOverlay ? handleAddOverlay : undefined}
          onRemove={handleRemoveOverlay}
          presets={presets}
          activePreset={activePreset}
          onApplyPreset={canAddOverlay ? applyPreset : undefined}
          onSavePreset={canAddOverlay ? handleSavePreset : undefined}
          onToggleAll={handleToggleAll}
          allHidden={allHidden}
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
        {/* The LAYERS checklist is retired — layer control lives in the inline
            legend (phases 2/3). The right dock is now a collapsible, contextual
            symbol-details panel (Plan 0096 phase 4). */}
        <ChartSidePanel symbol={symbol} bars={bars} quote={quote} />
      </div>
    </div>
  )
}
