/**
 * useChartSync — the declarative forward effects that drive the imperative
 * ChartController from React state (Plan 0098 thin-C), lifted out of CandlestickChart
 * so the component stays a thin adapter. One effect per controller method: mount /
 * dispose lifecycle, setBars, the overlay/oscillator/price-line reconcilers, the
 * primitive feeds (trendlines / ichimoku / divergences / market-structure), the fib/
 * pivot + anchored-VWAP structure reconcilers, the forming bar, the axis, and the
 * in-place restyle — in the order the chart's invariants require (OBV pane before
 * oscillators before divergences; market structure before the component's candlestick
 * markers). Returns the two structure reconciles' drawn levels/points for the tooltip.
 *
 * `setMarkers` deliberately stays in the component: it needs the clicked-bar ts that
 * `useChartGestures` produces, which is only available after the chart mounts.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { MutableRefObject, RefObject } from 'react'

import type { ChartController } from '../lib/chart/controller'
import { requiredOscillatorKindsFor } from '../lib/divergences'
import { mainSeriesKind } from '../lib/chartSeries'
import { getCandleType, type CandleSeriesType } from '../lib/chartStyle'
import type { MarketStructureResult } from '../lib/marketStructure'
import type { HoverableLevel, StructureMarkerPoint } from '../lib/tooltip'
import type { EffectiveTheme } from '../lib/theme'
import type { Bar } from '../types/sidecar/bar'
import type { Divergence, OverlaySpec, TrendlineSpec } from '../types/events'
import type { QuoteResponse } from '../types/sidecar/quote-response'

export interface UseChartSyncParams {
  containerRef: RefObject<HTMLDivElement>
  bars: Bar[]
  effectiveOverlays: ReadonlyArray<OverlaySpec>
  hidden: ReadonlySet<string>
  divergences: ReadonlyArray<Divergence>
  quote: QuoteResponse | null | undefined
  timeframe: string | undefined
  effectiveTheme: EffectiveTheme
  /** Live theme without re-running the reconcile effects (a flip recolours in place
   * via `restyle`); the reconcilers read this, not `effectiveTheme`. */
  effectiveThemeRef: MutableRefObject<EffectiveTheme>
  styleVersion: number
  candleType: CandleSeriesType
  shownTrendlines: ReadonlyArray<TrendlineSpec>
  highlightedTrendlineKey: string | null
  marketStructureResult: MarketStructureResult
}

export interface ChartSyncResult {
  structureLevels: HoverableLevel[]
  structureMarkerPoints: StructureMarkerPoint[]
}

export function useChartSync(
  controller: ChartController,
  {
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
  }: UseChartSyncParams,
): ChartSyncResult {
  // Reflect what's drawn into the test hook. Stable identity (reads only the stable
  // controller), so it can sit in effect dep arrays without retriggering them.
  const syncTestRenderHook = useCallback((): void => {
    const kinds: Array<{ kind: string; period?: number | null }> = []
    if (controller.seriesRef.current !== null) {
      kinds.push({ kind: mainSeriesKind(getCandleType()) })
    }
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
  // (the series type is fixed at creation). The theme ref gives the current theme
  // without making this effect re-run — a flip recolours in place (restyle effect).
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    controller.mount(container, { candleType, theme: effectiveThemeRef.current })
    syncTestRenderHook()
    return () => {
      controller.dispose()
      syncTestRenderHook()
    }
  }, [controller, containerRef, effectiveThemeRef, syncTestRenderHook, candleType])

  // Push bar data (scroll-anchored prepend vs fit-on-genuine-change handled inside).
  useEffect(() => {
    controller.setBars(bars)
    syncTestRenderHook()
  }, [controller, bars, syncTestRenderHook, candleType])

  // Price-pane overlay reconcile (ema/sma + supertrend + bbands). Keyed on
  // bars/overlays/hidden, NOT the theme (existing series recolour via restyle).
  useEffect(() => {
    controller.setOverlays({
      bars,
      overlays: effectiveOverlays,
      hidden,
      theme: effectiveThemeRef.current,
    })
    syncTestRenderHook()
  }, [
    controller,
    bars,
    effectiveOverlays,
    hidden,
    effectiveThemeRef,
    candleType,
    syncTestRenderHook,
  ])

  // Ichimoku cloud primitive + reserved right-edge space.
  useEffect(() => {
    controller.setIchimoku({ bars, overlays: effectiveOverlays, hidden })
  }, [controller, bars, effectiveOverlays, hidden, effectiveTheme, candleType])

  // OBV pane lazy lifecycle — runs before the oscillator panes (it claims pane 0).
  useEffect(() => {
    controller.setObv({ bars, hidden, divergences, theme: effectiveThemeRef.current })
    syncTestRenderHook()
  }, [controller, bars, hidden, divergences, effectiveThemeRef, candleType, syncTestRenderHook])

  // Oscillator panes a divergence needs, ensured even when the overlay is off.
  const requiredOscillatorKinds = useMemo(
    () => requiredOscillatorKindsFor(divergences),
    [divergences],
  )
  // Oscillator sub-panes — runs after the OBV pane so oscillators take the slots below.
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

  // Divergence segments — runs after the OBV + oscillator panes exist.
  useEffect(() => {
    controller.setDivergences(divergences)
  }, [controller, divergences, candleType])

  // Live forming-bar update.
  useEffect(() => {
    controller.setQuote(quote, bars, timeframe)
  }, [controller, quote, bars, timeframe, candleType])

  // Monthly axis ticks (`1mo` only).
  useEffect(() => {
    controller.setTimeframeAxis(timeframe)
  }, [controller, timeframe, candleType])

  // Trendline primitive feed (`effectiveTheme` re-reads the colour tokens).
  useEffect(() => {
    controller.setTrendlines(shownTrendlines, highlightedTrendlineKey)
  }, [controller, shownTrendlines, highlightedTrendlineKey, effectiveTheme, candleType])

  // Market-structure markers — runs BEFORE the component's setMarkers so the
  // candlestick markers own the last write. Returns the drawn points for the tooltip;
  // publish to state only when they move so a no-op reconcile doesn't re-render.
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

  // Agent `price_line` S/R levels — recolours in place, so keys on theme + styleVersion.
  useEffect(() => {
    controller.setPriceLines({ overlays: effectiveOverlays, hidden, theme: effectiveTheme })
  }, [controller, effectiveOverlays, hidden, effectiveTheme, styleVersion, candleType])

  // Fib/pivot horizontal price lines — returns the drawn levels for the tooltip.
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

  // Anchored-VWAP line series.
  useEffect(() => {
    controller.setAnchoredVwap({ bars, overlays: effectiveOverlays, hidden })
  }, [controller, bars, effectiveOverlays, hidden, candleType])

  // Re-apply colours + widths in place on a theme flip or chart-style mutation.
  useEffect(() => {
    controller.restyle(effectiveTheme)
  }, [controller, effectiveTheme, styleVersion, candleType])

  return { structureLevels, structureMarkerPoints }
}
