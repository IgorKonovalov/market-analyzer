/**
 * Lightweight-charts wrapper. Four effects, four responsibilities:
 *   1. Create the chart once on mount; dispose on unmount.
 *   2. Push data when `bars` change; never recreate the chart for new data.
 *   3. Reconcile overlay series (Plan 0007 phase 4.5) when `overlays` or
 *      `bars` change: add new line series, remove gone ones, recompute
 *      data for the kept ones.
 *   4. Push markers when `annotations` change; layer onto the candlestick.
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
import { useEffect, useMemo, useRef, useState } from 'react'
import { ColorType, createChart } from 'lightweight-charts'
import type {
  CandlestickData,
  IChartApi,
  ISeriesApi,
  MouseEventParams,
  SeriesMarker,
  Time,
  UTCTimestamp,
} from 'lightweight-charts'

import { toLightweightBar } from '../api/client'
import { postBarClicked, postRangeSelected } from '../api/uiEvents'
import { computeEma, computeSma } from '../lib/indicators'
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

const MARKER_LABEL_MAX = 24
const BULLISH_COLOR = '#16a34a'
const BEARISH_COLOR = '#dc2626'

// MVP overlay support. The envelope schema permits `rsi`/`macd`/`bbands`
// so an agent can request them, but the renderer logs-and-skips them
// until the corresponding indicator math + presentation lands.
const SUPPORTED_OVERLAY_KINDS: ReadonlySet<OverlaySpec['kind']> = new Set(['ema', 'sma'])

const OVERLAY_COLOR_EMA = '#2563eb'
const OVERLAY_COLOR_SMA = '#f97316'

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
}

/** Our chart uses `UTCTimestamp` (epoch seconds); convert to ISO for the
 * UI-event payloads. Non-numeric `Time` (business-day) never occurs for our
 * data — guarded so a stray value can't produce `Invalid Date`. */
function timeToIso(time: Time): string | null {
  return typeof time === 'number' && Number.isFinite(time)
    ? new Date(time * 1000).toISOString()
    : null
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

// A drag shorter than this many px is treated as a click, not a range select.
const MIN_RANGE_SELECT_PX = 3

/** Resolve the clicked bar's OHLC: prefer the click's `seriesData` (exact data
 * point), else fall back to matching `param.time` against the `bars` prop — so a
 * click that lands near but not precisely on a data point still resolves. */
function resolveOhlc(
  param: MouseEventParams,
  series: ISeriesApi<'Candlestick'>,
  bars: Bar[],
): { open: number; high: number; low: number; close: number } | null {
  const cd = param.seriesData.get(series) as CandlestickData | undefined
  if (cd && typeof cd.open === 'number') {
    return { open: cd.open, high: cd.high, low: cd.low, close: cd.close }
  }
  if (typeof param.time !== 'number') return null
  const time = param.time
  const bar = bars.find((b) => Math.floor(new Date(b.event_ts).getTime() / 1000) === time)
  return bar ? { open: bar.open, high: bar.high, low: bar.low, close: bar.close } : null
}

interface OverlayEntry {
  spec: OverlaySpec
  series: ISeriesApi<'Line'>
}

function overlayKey(spec: OverlaySpec): string {
  return `${spec.kind}:${spec.period ?? 'na'}`
}

function overlayColorFor(spec: OverlaySpec): string {
  if (spec.kind === 'ema') return OVERLAY_COLOR_EMA
  if (spec.kind === 'sma') return OVERLAY_COLOR_SMA
  return '#888888'
}

export function CandlestickChart({
  bars,
  annotations,
  overlays,
  ariaLabel,
  agentModeEnabled = false,
  symbol,
  timeframe,
}: Props): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const overlaySeriesRef = useRef<Map<string, OverlayEntry>>(new Map())
  // Always-on volume series (Plan 0027 phase 3).
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const volumeMaSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const vwapSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const obvSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)

  // `select-range` cursor mode: when active (and agent mode is ON), a drag
  // selects a date range to POST instead of panning the chart.
  const [selectRangeMode, setSelectRangeMode] = useState(false)
  // The selection rectangle, in px relative to the chart container. Set on
  // pointerdown, updated through the drag, and KEPT after release so the user
  // sees what's selected; cleared on the next drag, Escape, or leaving the mode.
  const [selection, setSelection] = useState<{ startX: number; endX: number } | null>(null)
  // The selected window's time range (ISO), for the detail label. Tracks the
  // drag live and persists with the rectangle after release.
  const [rangeLabel, setRangeLabel] = useState<{ start: string; end: string } | null>(null)
  // The event_ts (ISO) of the last clicked bar, marked on the chart so the user
  // sees which bar they picked. Time-anchored (a series marker), so it tracks
  // pan/zoom rather than drifting like a pixel overlay would.
  const [clickedBarTs, setClickedBarTs] = useState<string | null>(null)

  // The gesture handlers are wired once on mount but must read the *current*
  // props/state — refs keep them live without re-registering listeners.
  const agentModeRef = useRef(agentModeEnabled)
  agentModeRef.current = agentModeEnabled
  const selectRangeRef = useRef(selectRangeMode)
  selectRangeRef.current = selectRangeMode
  const symbolRef = useRef(symbol)
  symbolRef.current = symbol
  const timeframeRef = useRef(timeframe)
  timeframeRef.current = timeframe
  const barsRef = useRef(bars)
  barsRef.current = bars
  // Set true when a range drag completes, so the click lightweight-charts may
  // fire on that same pointerup doesn't also register as a bar-click. Reset on
  // the next pointerdown and after it's consumed once.
  const suppressClickRef = useRef(false)

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
    window.__test_chart_render__ = { seriesCount: kinds.length, seriesKinds: kinds }
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

    // --- Plan 0014 UI gestures (gated on agent mode via refs) --------------- //

    // Bar-click: fires whenever agent mode is ON (independent of select-range
    // mode — a click is not a drag). The OHLC comes from the click's seriesData
    // when available, falling back to a lookup in the bars prop by timestamp so
    // a click that doesn't resolve exactly onto a data point still works.
    const handleClick = (param: MouseEventParams): void => {
      if (suppressClickRef.current) {
        suppressClickRef.current = false // consume the click that trailed a drag
        return
      }
      if (!agentModeRef.current || !symbolRef.current || !timeframeRef.current) return
      if (param.time === undefined) return
      const eventTs = timeToIso(param.time)
      if (eventTs === null) return
      const ohlc = resolveOhlc(param, series, barsRef.current)
      if (ohlc === null) return
      setClickedBarTs(eventTs) // mark the bar on the chart
      void postBarClicked({
        symbol: symbolRef.current,
        timeframe: timeframeRef.current,
        event_ts: eventTs,
        ...ohlc,
      })
    }
    chart.subscribeClick(handleClick)

    // Range-select: in range mode, a pointer drag over the chart maps the start
    // and end x-coordinates to bar times and POSTs the [start, end] window.
    // lightweight-charts drives pan/zoom via POINTER events and preventDefaults
    // pointerdown (which also suppresses the compat mouse events) — so the
    // selection MUST use pointer events, and the chart's pan is disabled in
    // range mode (the `handleScroll`/`handleScale` effect below) so the drag is
    // free to define a selection rather than scroll. Escape cancels + exits.
    let dragStartX: number | null = null
    const xInContainer = (clientX: number): number =>
      clientX - container.getBoundingClientRect().left
    const rangeFromX = (aX: number, bX: number): { start: string; end: string } | null => {
      const timeScale = chartRef.current?.timeScale()
      if (!timeScale) return null
      const t1 = timeScale.coordinateToTime(Math.min(aX, bX))
      const t2 = timeScale.coordinateToTime(Math.max(aX, bX))
      if (t1 === null || t2 === null) return null
      const start = timeToIso(t1)
      const end = timeToIso(t2)
      if (start === null || end === null) return null
      return { start, end }
    }
    const onPointerDown = (e: PointerEvent): void => {
      // Every fresh interaction clears a stale drag-suppression flag, so a
      // never-consumed suppression can't swallow a later genuine bar-click.
      suppressClickRef.current = false
      if (!agentModeRef.current || !selectRangeRef.current) return
      dragStartX = xInContainer(e.clientX)
      // Starting a new selection clears any previous one (and the click marker).
      setSelection({ startX: dragStartX, endX: dragStartX })
      setRangeLabel(null)
      setClickedBarTs(null)
      try {
        container.setPointerCapture(e.pointerId)
      } catch {
        // jsdom / unsupported environments — capture is a nicety (keeps the
        // drag alive if the pointer leaves the chart), not required for the POST.
      }
    }
    const onPointerMove = (e: PointerEvent): void => {
      if (dragStartX === null) return
      const endX = xInContainer(e.clientX)
      setSelection({ startX: dragStartX, endX })
      setRangeLabel(rangeFromX(dragStartX, endX))
    }
    const onPointerUp = (e: PointerEvent): void => {
      if (dragStartX === null) return
      const startX = dragStartX
      const endX = xInContainer(e.clientX)
      dragStartX = null
      // A click-sized drag is not a range — discard it (no marker, no POST) and
      // let it fall through to the bar-click handler.
      if (Math.abs(endX - startX) < MIN_RANGE_SELECT_PX) {
        setSelection(null)
        setRangeLabel(null)
        return
      }
      // A real range drag: suppress the click lightweight-charts may fire on
      // this same release so it doesn't double as a bar-click.
      suppressClickRef.current = true
      // Keep the rectangle + label after release so the user sees the selection.
      setSelection({ startX, endX })
      if (!agentModeRef.current || !selectRangeRef.current) return
      const range = rangeFromX(startX, endX)
      if (range === null || !symbolRef.current || !timeframeRef.current) return
      setRangeLabel(range)
      void postRangeSelected({
        symbol: symbolRef.current,
        timeframe: timeframeRef.current,
        range_start: range.start,
        range_end: range.end,
      })
    }
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape') return
      dragStartX = null // cancel any in-progress selection
      setSelection(null)
      setRangeLabel(null)
      setClickedBarTs(null) // clear the bar-click marker too
      setSelectRangeMode(false) // Escape exits the mode + clears the marker
    }
    container.addEventListener('pointerdown', onPointerDown)
    container.addEventListener('pointermove', onPointerMove)
    container.addEventListener('pointerup', onPointerUp)
    window.addEventListener('keydown', onKeyDown)

    return () => {
      chart.unsubscribeClick(handleClick)
      container.removeEventListener('pointerdown', onPointerDown)
      container.removeEventListener('pointermove', onPointerMove)
      container.removeEventListener('pointerup', onPointerUp)
      window.removeEventListener('keydown', onKeyDown)
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

  // Disable the chart's built-in pan/zoom while range-selecting so a drag
  // defines a selection instead of scrolling the chart; restore it otherwise.
  // Without this, lightweight-charts' pointer-driven pan eats the drag.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const interactive = !(agentModeEnabled && selectRangeMode)
    chart.applyOptions({ handleScroll: interactive, handleScale: interactive })
    if (interactive) {
      // Left select-range mode: drop the marker — once panning is re-enabled the
      // pixel-positioned rectangle would no longer line up with its bars.
      setSelection(null)
      setRangeLabel(null)
    }
  }, [agentModeEnabled, selectRangeMode])

  useEffect(() => {
    const chart = chartRef.current
    const candlestick = seriesRef.current
    if (!chart || !candlestick) return

    candlestick.setData(bars.map(toLightweightBar))

    // Always-on volume series, derived client-side from the same `bars`. Empty
    // `bars` yields empty arrays (no NaN/Infinity reaches lightweight-charts).
    volumeSeriesRef.current?.setData(computeVolumeBars(bars))
    volumeMaSeriesRef.current?.setData(computeVolumeMa(bars, VOLUME_MA_PERIOD))
    vwapSeriesRef.current?.setData(computeVwap(bars, VWAP_PERIOD))
    obvSeriesRef.current?.setData(computeObv(bars))

    const desired = new Map<string, OverlaySpec>()
    for (const spec of overlays ?? []) {
      if (!SUPPORTED_OVERLAY_KINDS.has(spec.kind)) {
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

    chart.timeScale().fitContent()
    syncTestRenderHook()
  }, [bars, overlays])

  // Agent mode off → the click marker's affordance is gone; clear it.
  useEffect(() => {
    if (!agentModeEnabled) setClickedBarTs(null)
  }, [agentModeEnabled])

  const markers = useMemo(() => {
    const base = annotationsToMarkers(annotations ?? [])
    if (clickedBarTs === null) return base
    const time = Math.floor(new Date(clickedBarTs).getTime() / 1000) as UTCTimestamp
    const clicked: SeriesMarker<UTCTimestamp> = {
      time,
      position: 'aboveBar',
      shape: 'circle',
      color: OVERLAY_COLOR_EMA,
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
          onClick={() => setSelectRangeMode((v) => !v)}
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

function computeOverlayData(bars: Bar[], spec: OverlaySpec): ReturnType<typeof computeEma> {
  if (spec.kind === 'ema' && spec.period !== null && spec.period !== undefined) {
    return computeEma(bars, spec.period)
  }
  if (spec.kind === 'sma' && spec.period !== null && spec.period !== undefined) {
    return computeSma(bars, spec.period)
  }
  return []
}

/**
 * Map annotations to lightweight-charts series markers. Bullish goes
 * below the bar with an up-arrow; bearish goes above with a down-arrow.
 * Labels are truncated to ~MARKER_LABEL_MAX chars so a runaway agent
 * can't push a 5KB string into the chart tooltip layer.
 *
 * Returned markers are sorted ascending by time — lightweight-charts
 * requires this and will throw on out-of-order markers.
 *
 * Exported for direct unit testing of the kind->shape mapping.
 */
export function annotationsToMarkers(annotations: Annotation[]): SeriesMarker<UTCTimestamp>[] {
  return annotations
    .map((a) => {
      const time = Math.floor(new Date(a.event_ts).getTime() / 1000) as UTCTimestamp
      const text = a.label ? truncateLabel(a.label) : ''
      if (a.kind === 'bullish_marker') {
        return {
          time,
          position: 'belowBar' as const,
          shape: 'arrowUp' as const,
          color: BULLISH_COLOR,
          text,
        }
      }
      return {
        time,
        position: 'aboveBar' as const,
        shape: 'arrowDown' as const,
        color: BEARISH_COLOR,
        text,
      }
    })
    .sort((a, b) => (a.time as number) - (b.time as number))
}

function truncateLabel(label: string): string {
  return label.length <= MARKER_LABEL_MAX ? label : `${label.slice(0, MARKER_LABEL_MAX - 1)}…`
}
