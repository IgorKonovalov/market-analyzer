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
import { useEffect, useMemo, useRef } from 'react'
import { ColorType, createChart } from 'lightweight-charts'
import type { IChartApi, ISeriesApi, Logical, SeriesMarker, UTCTimestamp } from 'lightweight-charts'

import { toLightweightBar } from '../api/client'
import { useChartGestures } from '../hooks/useChartGestures'
import { useLazyHistoryTrigger } from '../hooks/useLazyHistoryTrigger'
import { annotationsToMarkers } from '../lib/markers'
import { computeOverlayData, isSupportedOverlay, overlayColorFor } from '../lib/overlays'
import {
  VOLUME_MA_PERIOD,
  VWAP_PERIOD,
  computeObv,
  computeVolumeBars,
  computeVolumeMa,
  computeVwap,
} from '../lib/volume'
import type { Annotation } from '../types/sidecar/annotation'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec } from '../types/events'
import styles from './CandlestickChart.module.css'

// The clicked-bar marker reuses the EMA blue; kept local since it's a gesture
// affordance, not an overlay color (those live in the overlay registry).
const CLICKED_MARKER_COLOR = '#2563eb'

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
const VOLUME_MA_COLOR = '#64748b'
const VWAP_COLOR = '#9333ea'
const OBV_COLOR = '#0891b2'
// Candles occupy the upper band; volume hugs the bottom; OBV gets a strip above it.
const PRICE_SCALE_MARGINS = { top: 0.05, bottom: 0.4 }
const VOLUME_SCALE_MARGINS = { top: 0.82, bottom: 0 }
const OBV_SCALE_MARGINS = { top: 0.62, bottom: 0.22 }

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

interface Props {
  bars: Bar[]
  annotations?: Annotation[]
  overlays?: ReadonlyArray<OverlaySpec>
  ariaLabel?: string
  /** Plan 0014: when true, chart gestures (range-select, bar-click) are
   * forwarded to the agent via `POST /ui_events`. Default false — the
   * `AgentModeToggle` owns this state and threads it down. */
  agentModeEnabled?: boolean
  /** Carried in the gesture payloads so the agent knows which chart fired. */
  symbol?: string
  timeframe?: string
  /** Plan 0030: fired when the user scrolls near the buffer's left edge so the
   * parent can fetch + prepend older bars. */
  onReachLeftEdge?: () => void
  /** Gate for the left-edge trigger — false while an older fetch is in flight
   * or the start of available history has been reached. */
  historyTriggerEnabled?: boolean
}

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

interface OverlayEntry {
  spec: OverlaySpec
  series: ISeriesApi<'Line'>
}

function overlayKey(spec: OverlaySpec): string {
  return `${spec.kind}:${spec.period ?? 'na'}`
}

export function CandlestickChart({
  bars,
  annotations,
  overlays,
  ariaLabel,
  agentModeEnabled = false,
  symbol,
  timeframe,
  onReachLeftEdge,
  historyTriggerEnabled = false,
}: Props): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  // First-bar timestamp (ms) of the previous render, to detect left-side growth.
  const prevFirstTsRef = useRef<number | null>(null)
  const overlaySeriesRef = useRef<Map<string, OverlayEntry>>(new Map())
  // Always-on volume series (Plan 0027 phase 3).
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const volumeMaSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const vwapSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const obvSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)

  function syncTestRenderHook(): void {
    const kinds: Array<{ kind: string; period?: number | null }> = []
    if (seriesRef.current !== null) {
      kinds.push({ kind: 'candlestick' })
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
      barCount: seriesRef.current !== null ? bars.length : 0,
    }
  }

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    // lightweight-charts hands these strings to canvas APIs that don't
    // resolve CSS variables; passing `var(--color-fg)` paints with the
    // browser's invalid-color fallback. Read the computed values once at
    // mount and feed real color strings in.
    const computed = getComputedStyle(container)
    const textColor = computed.getPropertyValue('--color-fg').trim() || '#1a1a1a'
    const borderColor = computed.getPropertyValue('--color-border').trim() || '#e5e5e5'

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor,
      },
      grid: {
        vertLines: { color: borderColor },
        horzLines: { color: borderColor },
      },
      timeScale: {
        timeVisible: false,
        secondsVisible: false,
      },
      autoSize: true,
    })
    const series = chart.addCandlestickSeries()

    // Always-on volume series (Plan 0027 phase 3). Created once at mount; their
    // data is pushed in the bars effect. Disposed by `chart.remove()` on unmount
    // alongside the candlestick (the chart owns all its series).
    const volumeSeries = chart.addHistogramSeries({
      priceScaleId: VOLUME_SCALE_ID,
      priceFormat: { type: 'volume' },
      priceLineVisible: false,
      lastValueVisible: false,
    })
    const volumeMaSeries = chart.addLineSeries({
      priceScaleId: VOLUME_SCALE_ID,
      color: VOLUME_MA_COLOR,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    const vwapSeries = chart.addLineSeries({
      priceScaleId: PRICE_SCALE_ID, // rides the main price scale alongside candles
      color: VWAP_COLOR,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    const obvSeries = chart.addLineSeries({
      priceScaleId: OBV_SCALE_ID,
      color: OBV_COLOR,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    // Push the candles into the upper band and dock each derived band on its own
    // overlay scale, so the volume/OBV strips don't share the price axis.
    chart.priceScale(PRICE_SCALE_ID).applyOptions({ scaleMargins: PRICE_SCALE_MARGINS })
    chart.priceScale(VOLUME_SCALE_ID).applyOptions({ scaleMargins: VOLUME_SCALE_MARGINS })
    chart.priceScale(OBV_SCALE_ID).applyOptions({ scaleMargins: OBV_SCALE_MARGINS })

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
    syncTestRenderHook()

    return () => {
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
      volumeSeriesRef.current = null
      volumeMaSeriesRef.current = null
      vwapSeriesRef.current = null
      obvSeriesRef.current = null
      overlayMap.clear()
      syncTestRenderHook()
    }
  }, [])

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

    candlestick.setData(bars.map(toLightweightBar))

    // Always-on volume series, derived client-side from the same `bars`. Empty
    // `bars` yields empty arrays (no NaN/Infinity reaches lightweight-charts).
    volumeSeriesRef.current?.setData(computeVolumeBars(bars))
    volumeMaSeriesRef.current?.setData(computeVolumeMa(bars, VOLUME_MA_PERIOD))
    vwapSeriesRef.current?.setData(computeVwap(bars, VWAP_PERIOD))
    obvSeriesRef.current?.setData(computeObv(bars))

    const desired = new Map<string, OverlaySpec>()
    for (const spec of overlays ?? []) {
      if (!isSupportedOverlay(spec.kind)) {
        console.warn(
          `[CandlestickChart] unsupported overlay kind "${spec.kind}" — ignored (MVP renders ema/sma only)`,
        )
        continue
      }
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
        const series = chart.addLineSeries({
          color: overlayColorFor(spec),
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: false,
        })
        entry = { spec, series }
        overlaySeriesRef.current.set(key, entry)
      }
      entry.series.setData(computeOverlayData(bars, spec))
    }

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
    } else {
      chart.timeScale().fitContent()
    }
    prevFirstTsRef.current = newFirstMs
    syncTestRenderHook()
  }, [bars, overlays])

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
  })

  const markers = useMemo(() => {
    const base = annotationsToMarkers(annotations ?? [])
    if (clickedBarTs === null) return base
    const time = Math.floor(new Date(clickedBarTs).getTime() / 1000) as UTCTimestamp
    const clicked: SeriesMarker<UTCTimestamp> = {
      time,
      position: 'aboveBar',
      shape: 'circle',
      color: CLICKED_MARKER_COLOR,
      text: clickedBarTs.slice(0, 10),
    }
    // setMarkers requires ascending time order.
    return [...base, clicked].sort((a, b) => (a.time as number) - (b.time as number))
  }, [annotations, clickedBarTs])

  useEffect(() => {
    const series = seriesRef.current
    if (!series) return
    series.setMarkers(markers)
  }, [markers])

  return (
    <div className={styles.wrapper}>
      {agentModeEnabled && (
        <button
          type="button"
          data-testid="select-range-toggle"
          aria-pressed={selectRangeMode}
          className={styles.selectRangeButton}
          onClick={toggleSelectRange}
        >
          {selectRangeMode ? 'Selecting range… (Esc to cancel)' : 'Select range'}
        </button>
      )}
      <div className={styles.chartArea}>
        <div
          ref={containerRef}
          className={`${styles.chartContainer} ${selectRangeMode ? styles.selectRangeActive : ''}`.trim()}
          data-testid="candlestick-chart"
          role="img"
          aria-label={ariaLabel ?? `Candlestick chart, ${bars.length} bars`}
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
      </div>
    </div>
  )
}
