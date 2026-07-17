/**
 * CandlestickChart — the thin React adapter over the imperative chart core
 * (Plan 0098 / ADR-0092). All lightweight-charts wiring — the chart instance, series,
 * panes, primitives, the overlay / oscillator / OBV / fib-pivot / anchored-VWAP /
 * market-structure reconcilers, restyle, the axis and the forming bar — lives in a
 * plain-TS `ChartController` (`lib/chart/`); the component imports no `lightweight-
 * charts` types at all. It is decomposed into three concerns:
 *   - `useLayersControl` — the layers-legend control surface: the user-overlay +
 *     layer-visibility + preset stores, the candlestick-marker groups, the two-legend
 *     routing, and the built layer descriptors / legend values. Produces the `hidden`
 *     set every draw path consumes and the `<ChartLegend>` props.
 *   - `useChartSync` — the declarative forward effects driving the controller (one per
 *     method, in invariant order), returning the structure reconciles' drawn levels /
 *     points for the tooltip.
 *   - this component — builds the controller, calls the two hooks, keeps only the
 *     React-state hooks that need the live chart (gestures / tooltip / scans / lazy-
 *     history / drawing tools) + the `setMarkers` effect (which needs the gesture
 *     hook's clicked-bar ts), and renders the JSX.
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
import { ChartLegend } from './ChartLegend'
import { ChartSidePanel } from './ChartSidePanel'
import { ChartToolbar } from './ChartToolbar'
import { ChartTooltip } from './ChartTooltip'
import { MarketStructureBadge } from './MarketStructureBadge'
import { MARKET_STRUCTURE_LAYER_ID } from '../lib/chartSeries'
import { priceLineId } from '../lib/priceLines'
import { formatRangeLabel } from '../lib/chartAxis'
import { useDrawingTools } from '../hooks/useDrawingTools'
import { useLayersControl } from '../hooks/useLayersControl'
import { useChartSync } from '../hooks/useChartSync'
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

  // Drive the imperative controller from React state (Plan 0098 thin-C:
  // `useChartSync`): one forward effect per controller method, in the order the
  // chart's invariants require. Returns the fib/pivot + market-structure reconciles'
  // drawn levels/points for the hover tooltip. Called BEFORE the gesture hooks so its
  // mount effect runs first (they read the chart on mount); `setMarkers` stays below
  // because it needs the gesture hook's clicked-bar ts.
  const { structureLevels, structureMarkerPoints } = useChartSync(controller, {
    containerRef,
    bars,
    effectiveOverlays,
    hidden,
    divergences,
    quote,
    timeframe,
    effectiveTheme,
    effectiveThemeRef,
    styleVersion,
    candleType,
    shownTrendlines,
    highlightedTrendlineKey,
    marketStructureResult,
  })

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

  // Candlestick markers + pattern-span band (Plan 0049 phases 7 & 10 / Plan 0071
  // phase 2): draw only the enabled groups' markers + spans, themed, with the
  // clicked-bar affordance and hover emphasis. Stays here (not in `useChartSync`)
  // because it needs the gesture hook's `clickedBarTs`; runs after `useChartSync`'s
  // market-structure feed so the candlestick markers own the last series-markers write.
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

  // Hover tooltip (Plan 0047 phase 8 / Plan 0067 phase 2): crosshair-driven
  // marker/overlay/trendline read-out + pattern-bar outline (Plan 0072 phase 8:
  // `useChartTooltip` owns the state and returns it).
  // Visible agent `price_line` levels feed the nearest-level hover, so a
  // resistance/support line discloses its label + price.
  const priceLineLevels = useMemo(
    () =>
      effectiveOverlays
        .filter((o) => o.kind === 'price_line' && o.price != null && !hidden.has(priceLineId(o)))
        .map((o) => ({ title: o.label ?? 'Level', price: o.price as number })),
    [effectiveOverlays, hidden],
  )
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
      supertrendSeriesRef: controller.supertrendSeriesRef,
      ichimokuPrimitiveRef: controller.ichimokuPrimitiveRef,
      priceLineLevels,
      rebuildToken: candleType,
    },
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
