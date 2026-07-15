/**
 * useLayersControl — the chart's layers-legend control surface (Plan 0098 thin-B),
 * lifted out of CandlestickChart so the component stays a thin adapter. Owns the
 * renderer-side state that produces the `<ChartLegend>` props and the `hidden` set
 * every draw path consumes: the sticky per-(symbol,timeframe) user-overlay store
 * merged with the agent overlays, the persisted layer-visibility store, the
 * candlestick-marker groups, the two-legend routing, the built layer descriptors +
 * live legend values, the presets, and the quick toggle-all. No chart wiring — that
 * is the controller's; this is pure React state.
 */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import type { RefObject } from 'react'

import { marketStructure, type MarketStructureResult } from '../lib/marketStructure'
import { buildLegendValues } from '../lib/legendValues'
import { candleGroupKeyFromLayerId } from '../lib/candleGroups'
import { overlayLayerId } from '../lib/overlays'
import { dedupeTrendlines, patternStateKey, trendlineGroupLayerId } from '../lib/trendlines'
import { routeLayerHighlight, routeLayerToggle } from '../lib/chart/legendRouting'
import { useCandleMarkerGroups } from './useCandleMarkerGroups'
import { useLayersLegend } from './useLayersLegend'
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
import type { ChartMarker } from '../lib/markers'
import type { EffectiveTheme } from '../lib/theme'
import type { Bar } from '../types/sidecar/bar'
import type { OverlaySpec, TrendlineSpec } from '../types/events'

const NO_USER_OVERLAYS: OverlaySpec[] = []

export interface UseLayersControlParams {
  symbol: string | undefined
  timeframe: string | undefined
  overlays: ReadonlyArray<OverlaySpec> | undefined
  annotations: ChartMarker[] | undefined
  trendlines: ReadonlyArray<TrendlineSpec>
  bars: Bar[]
  effectiveTheme: EffectiveTheme
  styleVersion: number
  containerRef: RefObject<HTMLDivElement>
}

export function useLayersControl({
  symbol,
  timeframe,
  overlays,
  annotations,
  trendlines,
  bars,
  effectiveTheme,
  styleVersion,
  containerRef,
}: UseLayersControlParams) {
  // User-originated overlays (Plan 0082 phase 3, ADR-0077): renderer-owned, keyed by
  // (symbol, timeframe), merged with the agent's `overlays` prop. STICKY — an agent
  // redraw replaces only the prop, so the user's indicators survive.
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
  const merged = useMemo(() => mergeOverlays(overlays, userOverlays), [overlays, userOverlays])
  const effectiveOverlays = merged.overlays

  // Price-action market structure (Plan 0092 phase 6): computed client-side, feeding
  // the markers + the structural-trend badge. A second, distinct trend read.
  const marketStructureResult: MarketStructureResult = useMemo(() => marketStructure(bars), [bars])
  const hasMarketStructure =
    marketStructureResult.labeledPivots.length > 0 || marketStructureResult.events.length > 0
  const canAddOverlay = Boolean(symbol && timeframe)

  // Persisted per-(symbol,timeframe) layer visibility (Plan 0096 phase 3, ADR-0089).
  // A symbol-less chart (no bucket) keeps the ephemeral, all-visible behaviour.
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

  // The applied-preset name (Plan 0096 phase 3): shown until the layout diverges,
  // then "Custom". A fresh bucket opens on Clean; any tweak clears it.
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
  // Toggle a layer's visibility: persisted store when keyed, else the ephemeral
  // fallback. Any manual toggle diverges from an applied preset → "Custom".
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
  // Collapse the forming+confirmed twin of each geometry (Plan 0067 ph1) before
  // anything consumes the specs; drop the specs whose group row is unchecked.
  const visibleTrendlines = useMemo(() => dedupeTrendlines(trendlines), [trendlines])
  const shownTrendlines = useMemo(
    () => visibleTrendlines.filter((s) => !hidden.has(trendlineGroupLayerId(patternStateKey(s)))),
    [visibleTrendlines, hidden],
  )
  const [highlightedTrendlineKey, setHighlightedTrendlineKey] = useState<string | null>(null)
  // Candlestick-marker groups (Plan 0071 phase 2).
  const {
    candleGroups,
    enabledCandleGroups,
    drawnMarkers,
    highlightedCandleGroup,
    setHighlightedCandleGroup,
    toggleCandleGroup,
    candleKeySet,
  } = useCandleMarkerGroups(annotations, hidden)
  // Dispatch the two-legend routing decision (pure `legendRouting`).
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

  // Build the layers-legend descriptor list (Plan 0072 phase 8: `useLayersLegend`).
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
  // Live last-bar values for the inline legend (Plan 0096 phase 2).
  const legendValues = useMemo(
    () => buildLegendValues(bars, effectiveOverlays, bars.length > 0),
    [bars, effectiveOverlays],
  )

  // Chart presets (Plan 0096 phase 3, ADR-0089).
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

  // Quick toggle-all (post-0105): hide every hidden-set-governed layer, then restore
  // the prior mix. The stash is per-bucket, in-memory only.
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

  return {
    effectiveOverlays,
    hidden,
    marketStructureResult,
    hasMarketStructure,
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
  }
}
