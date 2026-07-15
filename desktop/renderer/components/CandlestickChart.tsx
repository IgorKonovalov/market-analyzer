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
 * The reconcilers added after this plan was drafted — Plan 0092's fib/pivot price
 * lines + anchored VWAP + market-structure markers, Plan 0105's lazy OBV pane — are
 * folded in too (setStructureLevels / setAnchoredVwap / setMarketStructure / setObv),
 * so the component imports no `lightweight-charts` types at all. The two structure
 * reconciles return their drawn levels/points, which the component publishes to state
 * (change-detected) for the hover tooltip.
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

import { t } from '../lib/i18n'
import { useChartGestures } from '../hooks/useChartGestures'
import { useChartPatternRecompute } from '../hooks/useChartPatternRecompute'
import { useChartScans } from '../hooks/useChartScans'
import { useChartTooltip } from '../hooks/useChartTooltip'
import { useLazyHistoryTrigger } from '../hooks/useLazyHistoryTrigger'
import type { ChartMarker } from '../lib/markers'
import type { HoverableLevel, StructureMarkerPoint } from '../lib/tooltip'
import { ChartLegend } from './ChartLegend'
import { ChartSidePanel } from './ChartSidePanel'
import { ChartToolbar } from './ChartToolbar'
import { ChartTooltip } from './ChartTooltip'
import { MarketStructureBadge } from './MarketStructureBadge'
import { MARKET_STRUCTURE_LAYER_ID, mainSeriesKind } from '../lib/chartSeries'
import { formatRangeLabel } from '../lib/chartAxis'
import { requiredOscillatorKindsFor } from '../lib/divergences'
import { useDrawingTools } from '../hooks/useDrawingTools'
import { useLayersControl } from '../hooks/useLayersControl'
import { ChartController } from '../lib/chart/controller'
import { DrawingRail } from './DrawingRail'
import {
  getStoredTheme,
  resolveEffective,
  subscribeEffective,
  type EffectiveTheme,
} from '../lib/theme'
import { getCandleType, subscribeChartStyle } from '../lib/chartStyle'
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
  // Layers-legend control surface (Plan 0098 thin-B: `useLayersControl`): the
  // renderer-side overlay/visibility/preset/toggle-all state + the two-legend
  // routing. Produces the `hidden` set every draw path consumes, the merged
  // `effectiveOverlays`, the drawn candlestick markers, and the `<ChartLegend>` props.
  const {
    effectiveOverlays,
    hidden,
    marketStructureResult,
    drawnMarkers,
    shownTrendlines,
    highlightedTrendlineKey,
    highlightedCandleGroup,
    layers,
    legendValues,
    presets,
    activePreset,
    canAddOverlay,
    allHidden,
    onLayerToggle,
    onLayerHighlight,
    handleAddOverlay,
    handleRemoveOverlay,
    applyPreset,
    handleSavePreset,
    handleToggleAll,
  } = useLayersControl({
    symbol,
    timeframe,
    overlays,
    annotations,
    trendlines,
    bars,
    effectiveTheme,
    styleVersion,
    containerRef,
  })
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
    if (controller.obvSeriesRef.current !== null) kinds.push({ kind: 'obv' })
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
    syncTestRenderHook()
    return () => {
      // `controller.dispose()` (via chart.remove) disposes the chart, its series,
      // panes and primitives, and clears every reconciler's bookkeeping.
      controller.dispose()
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
  useEffect(() => {
    controller.setObv({ bars, hidden, divergences, theme: effectiveThemeRef.current })
    syncTestRenderHook()
  }, [controller, bars, hidden, divergences, candleType, syncTestRenderHook])
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
  // (Plan 0098 phase 3). Runs after the OBV + oscillator-pane reconciles so every
  // pane's divergence primitive exists.
  useEffect(() => {
    controller.setDivergences(divergences)
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

  // Market-structure markers (Plan 0092 phase 6, ADR-0084) — folded into the
  // controller (Plan 0098 thin-A). Runs BEFORE the setMarkers effect so the
  // candlestick-pattern markers own the last write to the series-markers capture.
  // The reconcile returns the drawn points for the tooltip's structure hover;
  // publish to state only when they move, so a no-op reconcile doesn't re-render.
  const [structureMarkerPoints, setStructureMarkerPoints] = useState<StructureMarkerPoint[]>([])
  useEffect(() => {
    const points = controller.setMarketStructure({
      structure: marketStructureResult,
      bars,
      hidden,
      theme: effectiveTheme,
    })
    setStructureMarkerPoints((prev) =>
      prev.length === points.length &&
      prev.every((p, i) => p.time === points[i].time && p.label === points[i].label)
        ? prev
        : points,
    )
  }, [controller, marketStructureResult, bars, hidden, effectiveTheme, styleVersion, candleType])

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

  // Fib/pivot horizontal price lines (Plan 0092 phase 5) — folded into the
  // controller (Plan 0098 thin-A). The reconcile returns the drawn levels for the
  // nearest-level-on-hover tooltip lookup; publish them to state only when they
  // actually move, so a no-op reconcile doesn't re-render the chart.
  const [structureLevels, setStructureLevels] = useState<HoverableLevel[]>([])
  useEffect(() => {
    const levels = controller.setStructureLevels({ bars, overlays: effectiveOverlays, hidden })
    setStructureLevels((prev) =>
      prev.length === levels.length &&
      prev.every((l, i) => l.title === levels[i].title && l.price === levels[i].price)
        ? prev
        : levels,
    )
  }, [controller, bars, effectiveOverlays, hidden, candleType])
  // Anchored-VWAP line series (Plan 0092 phase 5) — folded into the controller.
  useEffect(() => {
    controller.setAnchoredVwap({ bars, overlays: effectiveOverlays, hidden })
  }, [controller, bars, effectiveOverlays, hidden, candleType])

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
    controller.restyle(effectiveTheme)
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
