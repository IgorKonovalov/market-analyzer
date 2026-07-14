/**
 * The drawing dock's tool-mode state machine + edit engine (Plan 0097 phase 2,
 * ADR-0091).
 *
 * Owns the user-drawing interaction on the chart:
 *   - an ACTIVE TOOL suppresses pan/zoom; clicks place anchors; on the last
 *     anchor the drawing is created, persisted (`ma.userDrawings`), selected, and
 *     the tool returns to select mode. A live dashed preview follows the cursor.
 *   - SELECT mode (no active tool): click a drawing to select it, drag an endpoint
 *     handle to re-anchor it (snapped to the nearest bar's OHLC), Delete removes
 *     the selection, Esc deselects / cancels a placement.
 *
 * Mutually exclusive with `useChartGestures`' agent-range-select: the component
 * passes `selectRangeMode` in and gates the two so only one pointer machine is
 * armed (ADR-0091 risk note). Drawing mode owns pan while a tool is active or an
 * endpoint is being dragged; otherwise `useChartGestures` owns it.
 *
 * Feeds the `DrawingPrimitive` (attached in the chart-creation effect, the
 * trendline-primitive lifecycle) its drawings / selection / preview; the pure
 * geometry + hit-testing live in `lib/drawings.ts` and the snap in
 * `useDrawingHitTest`.
 */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import type { RefObject } from 'react'
import type { IChartApi, ISeriesApi } from 'lightweight-charts'

import type { DrawingKind, DrawingSpec, TimePricePoint } from '../types/events'
import type { Bar } from '../types/sidecar/bar'
import type { DrawingPrimitive } from '../lib/drawings'
import { POINT_COUNT_BY_KIND } from '../lib/drawings'
import {
  addUserDrawing,
  getUserDrawingsSnapshot,
  removeUserDrawing,
  subscribeUserDrawings,
  updateUserDrawing,
} from '../lib/userDrawings'
import { useDrawingHitTest } from './useDrawingHitTest'

/** A pointer movement under this many px (from pointerdown to pointerup) counts
 * as a click, not a drag — the placement/selection threshold. */
const CLICK_SLOP_PX = 3

/** A fresh drawing id; `crypto.randomUUID` in the Electron renderer (a secure
 * context), with a non-crypto fallback for jsdom/tests (ids are UI identity, not
 * on the financial-determinism path). */
function genDrawingId(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return `d-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e9).toString(36)}`
  }
}

export interface UseDrawingToolsParams {
  /** The symbol the drawings are keyed by (per-symbol, ADR-0091). Drawing is
   * inert until known. */
  symbol?: string
  bars: Bar[]
  /** `useChartGestures` range-select mode — for the pan hand-off and mutual
   * exclusion. */
  selectRangeMode: boolean
  /** The armed tool. Owned by the component (it coordinates this machine with
   * `useChartGestures`, which parks while a tool is armed). */
  activeTool: DrawingKind | null
  /** Raw setter for the armed tool (component state). The hook's own
   * `setActiveTool` wraps this with the placement/selection clean-up. */
  onActiveToolChange: (tool: DrawingKind | null) => void
  /** Rebuilt-chart token (candleType): re-feed the fresh primitive after a rebuild. */
  rebuildToken?: unknown
}

export interface UseDrawingToolsResult {
  /** Arm a drawing tool (or `null` to return to select mode). Arming a tool
   * cancels any in-progress placement and clears the selection. */
  setActiveTool: (tool: DrawingKind | null) => void
  selectedId: string | null
  /** Delete the selected drawing (no-op when nothing is selected). */
  deleteSelected: () => void
  /** Count of the current symbol's user drawings (for the rail / empty states). */
  drawingCount: number
}

interface DragState {
  id: string
  handleIndex: number
}

export function useDrawingTools(
  containerRef: RefObject<HTMLDivElement>,
  chartRef: RefObject<IChartApi | null>,
  seriesRef: RefObject<ISeriesApi<'Candlestick' | 'Bar' | 'Line' | 'Area'> | null>,
  primitiveRef: RefObject<DrawingPrimitive | null>,
  {
    symbol,
    bars,
    selectRangeMode,
    activeTool,
    onActiveToolChange,
    rebuildToken,
  }: UseDrawingToolsParams,
): UseDrawingToolsResult {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const { snapPixel } = useDrawingHitTest(chartRef, seriesRef, bars)

  const storeSnapshot = useSyncExternalStore(
    subscribeUserDrawings,
    getUserDrawingsSnapshot,
    getUserDrawingsSnapshot,
  )
  const drawings = useMemo<DrawingSpec[]>(
    () => (symbol ? (storeSnapshot[symbol] ?? EMPTY_DRAWINGS) : EMPTY_DRAWINGS),
    [storeSnapshot, symbol],
  )

  // In-flight gesture state (refs so they survive listener re-registration mid-
  // gesture): the first anchor of a multi-point placement, the active endpoint
  // drag, and the pointerdown origin for click-vs-drag discrimination.
  const pendingAnchorRef = useRef<TimePricePoint | null>(null)
  const dragRef = useRef<DragState | null>(null)
  const downPosRef = useRef<{ x: number; y: number } | null>(null)

  const setActiveTool = useCallback(
    (tool: DrawingKind | null): void => {
      pendingAnchorRef.current = null
      primitiveRef.current?.setPreview(null)
      if (tool !== null) setSelectedId(null)
      onActiveToolChange(tool)
    },
    [primitiveRef, onActiveToolChange],
  )

  const deleteSelected = useCallback((): void => {
    if (symbol === undefined || selectedId === null) return
    removeUserDrawing(symbol, selectedId)
    setSelectedId(null)
  }, [symbol, selectedId])

  // Feed the primitive the committed drawings + selection. Re-runs after a chart
  // rebuild (fresh primitive) via `rebuildToken`.
  useEffect(() => {
    primitiveRef.current?.setDrawings(drawings)
  }, [primitiveRef, drawings, rebuildToken])
  useEffect(() => {
    primitiveRef.current?.setSelectedId(selectedId)
  }, [primitiveRef, selectedId, rebuildToken])

  // Own pan for the drawing layer: suppressed while a tool is armed (a click
  // places an anchor instead of scrolling) and restored to the range-select
  // state otherwise. `useChartGestures` is suspended (touches no pan) whenever a
  // tool is armed, so this and its pan effect always agree on the value — no
  // fight. An endpoint drag toggles pan imperatively on top of this.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const interactive = activeTool === null && !selectRangeMode
    chart.applyOptions({ handleScroll: interactive, handleScale: interactive })
  }, [chartRef, activeTool, selectRangeMode, rebuildToken])

  // The pointer machine. Re-registers when the values it reads change so its
  // handlers close over current state (the `useChartGestures` pattern).
  useEffect(() => {
    const container = containerRef.current
    const chart = chartRef.current
    const primitive = primitiveRef.current
    if (!container || !chart || !primitive || symbol === undefined) return

    const xy = (e: PointerEvent): { x: number; y: number } => {
      const rect = container.getBoundingClientRect()
      return { x: e.clientX - rect.left, y: e.clientY - rect.top }
    }

    const commitPlacement = (anchor: TimePricePoint): void => {
      const kind = activeTool
      if (kind === null) return
      const needed = POINT_COUNT_BY_KIND[kind]
      const pending = pendingAnchorRef.current
      const anchors = pending === null ? [anchor] : [pending, anchor]
      if (anchors.length < needed) {
        pendingAnchorRef.current = anchor
        return
      }
      const spec: DrawingSpec = { kind, points: anchors, provenance: 'user', id: genDrawingId() }
      addUserDrawing(symbol, spec)
      pendingAnchorRef.current = null
      primitive.setPreview(null)
      setSelectedId(spec.id)
      onActiveToolChange(null) // return to select mode after placing (keep the new selection)
    }

    const onPointerDown = (e: PointerEvent): void => {
      const { x, y } = xy(e)
      downPosRef.current = { x, y }
      if (activeTool !== null) return // placement resolves on pointerup (click)

      // Select/edit mode: prefer grabbing a handle (of the selected drawing, or of
      // a freshly-hit one — a one-gesture endpoint grab), else select/deselect.
      let targetId = selectedId
      let handleIndex = targetId !== null ? primitive.hitTestHandle(targetId, x, y) : null
      if (handleIndex === null) {
        const hit = primitive.hitTestDrawingId(x, y)
        if (hit !== null) {
          targetId = hit
          if (hit !== selectedId) setSelectedId(hit)
          handleIndex = primitive.hitTestHandle(hit, x, y)
        } else {
          if (selectedId !== null) setSelectedId(null)
          targetId = null
        }
      }
      if (targetId !== null && handleIndex !== null) {
        dragRef.current = { id: targetId, handleIndex }
        chart.applyOptions({ handleScroll: false, handleScale: false })
        try {
          container.setPointerCapture(e.pointerId)
        } catch {
          /* capture is a nicety (keeps the drag alive off-chart), not required */
        }
      }
    }

    const onPointerMove = (e: PointerEvent): void => {
      const { x, y } = xy(e)
      const drag = dragRef.current
      if (drag !== null) {
        const anchor = snapPixel(x, y)
        if (anchor === null) return
        const spec = drawings.find((d) => d.id === drag.id)
        if (spec === undefined) return
        const points = spec.points.map((p, i) => (i === drag.handleIndex ? anchor : p))
        // Feed the working set directly (single clean line following the cursor);
        // committed to the store on release.
        primitive.setDrawings(drawings.map((d) => (d.id === drag.id ? { ...spec, points } : d)))
        return
      }
      if (activeTool !== null && pendingAnchorRef.current !== null) {
        const anchor = snapPixel(x, y)
        if (anchor === null) return
        primitive.setPreview({
          kind: activeTool,
          points: [pendingAnchorRef.current, anchor],
          provenance: 'user',
          id: 'preview',
        })
      }
    }

    const onPointerUp = (e: PointerEvent): void => {
      const { x, y } = xy(e)
      const down = downPosRef.current
      downPosRef.current = null
      const drag = dragRef.current
      if (drag !== null) {
        dragRef.current = null
        chart.applyOptions({ handleScroll: !selectRangeMode, handleScale: !selectRangeMode })
        const anchor = snapPixel(x, y)
        const spec = drawings.find((d) => d.id === drag.id)
        if (anchor !== null && spec !== undefined) {
          const points = spec.points.map((p, i) => (i === drag.handleIndex ? anchor : p))
          updateUserDrawing(symbol, { ...spec, points })
        } else {
          primitive.setDrawings(drawings) // couldn't snap → revert the working set
        }
        return
      }
      if (activeTool === null) return
      // Placement click: ignore a drag-sized movement (that isn't a click).
      if (down !== null && Math.hypot(x - down.x, y - down.y) > CLICK_SLOP_PX) return
      const anchor = snapPixel(x, y)
      if (anchor === null) return
      commitPlacement(anchor)
    }

    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        if (activeTool !== null || pendingAnchorRef.current !== null) {
          pendingAnchorRef.current = null
          primitive.setPreview(null)
          onActiveToolChange(null)
        } else if (selectedId !== null) {
          setSelectedId(null)
        }
        return
      }
      if (
        (e.key === 'Delete' || e.key === 'Backspace') &&
        activeTool === null &&
        selectedId !== null
      ) {
        removeUserDrawing(symbol, selectedId)
        setSelectedId(null)
      }
    }

    container.addEventListener('pointerdown', onPointerDown)
    container.addEventListener('pointermove', onPointerMove)
    container.addEventListener('pointerup', onPointerUp)
    window.addEventListener('keydown', onKeyDown)
    return () => {
      container.removeEventListener('pointerdown', onPointerDown)
      container.removeEventListener('pointermove', onPointerMove)
      container.removeEventListener('pointerup', onPointerUp)
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [
    containerRef,
    chartRef,
    primitiveRef,
    symbol,
    bars,
    drawings,
    activeTool,
    onActiveToolChange,
    selectedId,
    selectRangeMode,
    snapPixel,
    rebuildToken,
  ])

  return { setActiveTool, selectedId, deleteSelected, drawingCount: drawings.length }
}

const EMPTY_DRAWINGS: DrawingSpec[] = []
