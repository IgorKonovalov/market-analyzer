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
  return typeof time === 'number' ? new Date(time * 1000).toISOString() : null
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

  // `select-range` cursor mode: when active (and agent mode is ON), a drag
  // selects a date range to POST instead of panning the chart.
  const [selectRangeMode, setSelectRangeMode] = useState(false)

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

  function syncTestRenderHook(): void {
    const kinds: Array<{ kind: string; period?: number | null }> = []
    if (seriesRef.current !== null) {
      kinds.push({ kind: 'candlestick' })
    }
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

    chartRef.current = chart
    seriesRef.current = series
    // Capture the Map reference into a local for the cleanup closure
    // (react-hooks/exhaustive-deps: ref.current may change between effect
    // run and cleanup invocation; the local capture is the canonical fix).
    const overlayMap = overlaySeriesRef.current
    syncTestRenderHook()

    // --- Plan 0014 UI gestures (gated on agent mode via refs) --------------- //

    // Bar-click: only when agent mode is ON and we're NOT in range-select mode
    // (in range mode the drag owns the pointer). Resolves to the candlestick
    // series' bar regardless of which overlay was hovered.
    const handleClick = (param: MouseEventParams): void => {
      if (!agentModeRef.current || selectRangeRef.current) return
      if (param.time === undefined || !symbolRef.current || !timeframeRef.current) return
      const candleData = param.seriesData.get(series) as CandlestickData | undefined
      if (!candleData) return
      const eventTs = timeToIso(param.time)
      if (eventTs === null) return
      void postBarClicked({
        symbol: symbolRef.current,
        timeframe: timeframeRef.current,
        event_ts: eventTs,
        open: candleData.open,
        high: candleData.high,
        low: candleData.low,
        close: candleData.close,
      })
    }
    chart.subscribeClick(handleClick)

    // Range-select: in range mode, mousedown→mouseup over the chart maps the
    // two x-coordinates to bar times and POSTs the [start, end] window. Listen
    // in the capture phase + stopPropagation so lightweight-charts doesn't also
    // pan while we're selecting. Escape cancels the in-progress drag and exits
    // the mode.
    let dragStartX: number | null = null
    const xInContainer = (clientX: number): number =>
      clientX - container.getBoundingClientRect().left
    const onMouseDown = (e: MouseEvent): void => {
      if (!agentModeRef.current || !selectRangeRef.current) return
      dragStartX = xInContainer(e.clientX)
      e.stopPropagation()
    }
    const onMouseUp = (e: MouseEvent): void => {
      if (!agentModeRef.current || !selectRangeRef.current || dragStartX === null) return
      const endX = xInContainer(e.clientX)
      const startX = dragStartX
      dragStartX = null
      e.stopPropagation()
      const timeScale = chartRef.current?.timeScale()
      if (!timeScale || !symbolRef.current || !timeframeRef.current) return
      const t1 = timeScale.coordinateToTime(Math.min(startX, endX))
      const t2 = timeScale.coordinateToTime(Math.max(startX, endX))
      if (t1 === null || t2 === null) return
      const rangeStart = timeToIso(t1)
      const rangeEnd = timeToIso(t2)
      if (rangeStart === null || rangeEnd === null) return
      void postRangeSelected({
        symbol: symbolRef.current,
        timeframe: timeframeRef.current,
        range_start: rangeStart,
        range_end: rangeEnd,
      })
    }
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape') return
      dragStartX = null // cancel any in-progress selection
      setSelectRangeMode(false) // Escape exits the mode
    }
    container.addEventListener('mousedown', onMouseDown, true)
    container.addEventListener('mouseup', onMouseUp, true)
    window.addEventListener('keydown', onKeyDown)

    return () => {
      chart.unsubscribeClick(handleClick)
      container.removeEventListener('mousedown', onMouseDown, true)
      container.removeEventListener('mouseup', onMouseUp, true)
      window.removeEventListener('keydown', onKeyDown)
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
      overlayMap.clear()
      syncTestRenderHook()
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    const candlestick = seriesRef.current
    if (!chart || !candlestick) return

    candlestick.setData(bars.map(toLightweightBar))

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

  const markers = useMemo(() => annotationsToMarkers(annotations ?? []), [annotations])

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
      <div
        ref={containerRef}
        className={`${styles.chartContainer} ${selectRangeMode ? styles.selectRangeActive : ''}`.trim()}
        data-testid="candlestick-chart"
        role="img"
        aria-label={ariaLabel ?? `Candlestick chart, ${bars.length} bars`}
      />
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
