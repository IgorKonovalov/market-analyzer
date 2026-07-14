/**
 * Plan 0029 phase 1: the CandlestickChart pointer-gesture state machine,
 * lifted out of the component.
 *
 * Owns the renderer→agent chart gestures (Plan 0014, ADR-0021; forwarding is
 * unconditional per ADR-0101 — no agent-mode gate):
 *   - select-range drag → `postRangeSelected`, with a kept selection rectangle,
 *   - bar click → `postBarClicked`, with a click marker,
 *   - click-vs-drag suppression so a real drag doesn't double as a bar-click,
 *   - Escape to cancel the selection and leave select-range mode.
 *
 * Why a hook instead of inline effects: the old component jammed all of this
 * into a mount-once effect, which forced eight `useRef`s mirroring live props
 * (symbol/timeframe/bars/selectRangeMode) so the frozen handlers could read
 * current values. Here the gesture effect simply depends on the props it
 * reads, so the handlers close over current values directly — no prop-mirror
 * refs. The only refs left are genuine FSM state (`dragStartX`, the post-drag
 * click-suppression flag) that must survive listener re-registration.
 *
 * The chart and candlestick series are owned by the component (lifecycle +
 * dispose stay there per ADR-0008); this hook receives their refs. It MUST be
 * called after the component's chart-creation effect so that, on mount,
 * `chartRef`/`seriesRef` are populated before the gesture effect wires up.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import type {
  CandlestickData,
  IChartApi,
  ISeriesApi,
  MouseEventParams,
  Time,
} from 'lightweight-charts'

import { postBarClicked, postRangeSelected } from '../api/uiEvents'
import type { Bar } from '../types/sidecar/bar'

// A drag shorter than this many px is treated as a click, not a range select.
const MIN_RANGE_SELECT_PX = 3

export interface ChartSelection {
  startX: number
  endX: number
}

export interface ChartRangeLabel {
  start: string
  end: string
}

export interface UseChartGesturesParams {
  /** Carried in the gesture payloads so the agent knows which chart fired.
   * Gestures forward whenever both are present (ADR-0101 — always on). */
  symbol?: string
  timeframe?: string
  /** Used for the bar-click OHLC fallback when the click misses a data point. */
  bars: Bar[]
  /** When true, a drawing tool owns the pointer (Plan 0097 / ADR-0091): this
   * machine parks — no range-select drag, no bar-click subscription — and leaves
   * pan control to `useDrawingTools`, so the two pointer machines never fight. */
  suspended?: boolean
}

export interface UseChartGesturesResult {
  /** `select-range` cursor mode: a drag selects a window instead of panning. */
  selectRangeMode: boolean
  toggleSelectRange: () => void
  /** Selection rectangle in px relative to the chart container, or null. */
  selection: ChartSelection | null
  /** The selected window's ISO time range, for the detail label. */
  rangeLabel: ChartRangeLabel | null
  /** event_ts (ISO) of the last clicked bar, marked on the chart. */
  clickedBarTs: string | null
}

/** Our chart uses `UTCTimestamp` (epoch seconds); convert to ISO for the
 * UI-event payloads. Non-numeric `Time` (business-day) never occurs for our
 * data — guarded so a stray value can't produce `Invalid Date`. */
function timeToIso(time: Time): string | null {
  return typeof time === 'number' && Number.isFinite(time)
    ? new Date(time * 1000).toISOString()
    : null
}

/** Resolve the clicked bar's OHLC: prefer the click's `seriesData` (exact data
 * point), else fall back to matching `param.time` against the `bars` prop — so a
 * click that lands near but not precisely on a data point still resolves. */
function resolveOhlc(
  param: MouseEventParams,
  series: ISeriesApi<'Candlestick' | 'Bar' | 'Line' | 'Area'>,
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

export function useChartGestures(
  containerRef: RefObject<HTMLDivElement>,
  chartRef: RefObject<IChartApi | null>,
  // The main series is any of the four render types (Plan 0068 phase 4); a click
  // still resolves OHLC (from `seriesData` for candles/bars, else the `bars` prop).
  seriesRef: RefObject<ISeriesApi<'Candlestick' | 'Bar' | 'Line' | 'Area'> | null>,
  { symbol, timeframe, bars, suspended = false }: UseChartGesturesParams,
): UseChartGesturesResult {
  const [selectRangeMode, setSelectRangeMode] = useState(false)
  // The selection rectangle, in px. Set on pointerdown, updated through the
  // drag, and KEPT after release so the user sees what's selected; cleared on
  // the next drag, Escape, or leaving the mode.
  const [selection, setSelection] = useState<ChartSelection | null>(null)
  // The selected window's time range (ISO), for the detail label. Tracks the
  // drag live and persists with the rectangle after release.
  const [rangeLabel, setRangeLabel] = useState<ChartRangeLabel | null>(null)
  // The event_ts (ISO) of the last clicked bar, marked on the chart so the user
  // sees which bar they picked. Time-anchored (a series marker), so it tracks
  // pan/zoom rather than drifting like a pixel overlay would.
  const [clickedBarTs, setClickedBarTs] = useState<string | null>(null)

  // Genuine gesture FSM state (NOT prop mirrors): the in-progress drag origin
  // and the post-drag click-suppression flag must survive listener
  // re-registration when a prop changes mid-gesture, so they're refs.
  const dragStartXRef = useRef<number | null>(null)
  const suppressClickRef = useRef(false)

  const toggleSelectRange = useCallback(() => setSelectRangeMode((v) => !v), [])

  // Gesture wiring. Re-registers when the live props it reads change, so the
  // handlers close over current values directly — no prop-mirror refs. Guards
  // on the chart existing: the component's mount-once creation effect runs
  // first (this hook is called after it), so on mount the refs are populated.
  useEffect(() => {
    const container = containerRef.current
    const chart = chartRef.current
    const series = seriesRef.current
    if (!container || !chart || !series) return
    // A drawing tool owns the pointer — park this machine entirely (no listeners,
    // no click subscription) so a placement click can't also fire a bar-click.
    if (suspended) return

    // Bar-click: fires whenever symbol/timeframe are known (independent of
    // select-range mode — a click is not a drag). OHLC comes from the click's
    // seriesData when available, else a lookup in the bars prop by timestamp.
    const handleClick = (param: MouseEventParams): void => {
      if (suppressClickRef.current) {
        suppressClickRef.current = false // consume the click that trailed a drag
        return
      }
      if (!symbol || !timeframe) return
      if (param.time === undefined) return
      const eventTs = timeToIso(param.time)
      if (eventTs === null) return
      const ohlc = resolveOhlc(param, series, bars)
      if (ohlc === null) return
      setClickedBarTs(eventTs) // mark the bar on the chart
      void postBarClicked({ symbol, timeframe, event_ts: eventTs, ...ohlc })
    }
    chart.subscribeClick(handleClick)

    // Range-select: in range mode, a pointer drag maps start/end x-coordinates
    // to bar times and POSTs the [start, end] window. lightweight-charts drives
    // pan via POINTER events and preventDefaults pointerdown (suppressing compat
    // mouse events), so the selection MUST use pointer events; the chart's pan
    // is disabled in range mode (the pan/zoom effect below) so the drag defines
    // a selection rather than scrolling. Escape cancels + exits.
    const xInContainer = (clientX: number): number =>
      clientX - container.getBoundingClientRect().left
    const rangeFromX = (aX: number, bX: number): ChartRangeLabel | null => {
      const timeScale = chart.timeScale()
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
      if (!selectRangeMode) return
      const startX = xInContainer(e.clientX)
      dragStartXRef.current = startX
      // Starting a new selection clears any previous one (and the click marker).
      setSelection({ startX, endX: startX })
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
      const startX = dragStartXRef.current
      if (startX === null) return
      const endX = xInContainer(e.clientX)
      setSelection({ startX, endX })
      setRangeLabel(rangeFromX(startX, endX))
    }
    const onPointerUp = (e: PointerEvent): void => {
      const startX = dragStartXRef.current
      if (startX === null) return
      const endX = xInContainer(e.clientX)
      dragStartXRef.current = null
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
      if (!selectRangeMode) return
      const range = rangeFromX(startX, endX)
      if (range === null || !symbol || !timeframe) return
      setRangeLabel(range)
      void postRangeSelected({
        symbol,
        timeframe,
        range_start: range.start,
        range_end: range.end,
      })
    }
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape') return
      dragStartXRef.current = null // cancel any in-progress selection
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
    }
  }, [containerRef, chartRef, seriesRef, selectRangeMode, symbol, timeframe, bars, suspended])

  // Disable the chart's built-in pan/zoom while range-selecting so a drag
  // defines a selection instead of scrolling; restore it otherwise. Without
  // this, lightweight-charts' pointer-driven pan eats the drag. While suspended
  // (a drawing tool is armed), `useDrawingTools` owns pan — don't touch it here,
  // just drop any stale selection rectangle.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    if (suspended) {
      setSelection(null)
      setRangeLabel(null)
      return
    }
    const interactive = !selectRangeMode
    chart.applyOptions({ handleScroll: interactive, handleScale: interactive })
    if (interactive) {
      // Left select-range mode: drop the marker — once panning is re-enabled the
      // pixel-positioned rectangle would no longer line up with its bars.
      setSelection(null)
      setRangeLabel(null)
    }
  }, [chartRef, selectRangeMode, suspended])

  return { selectRangeMode, toggleSelectRange, selection, rangeLabel, clickedBarTs }
}
