/**
 * Persisted layer-visibility store (Plan 0096 phase 3, ADR-0089).
 *
 * Promotes the chart's formerly-ephemeral `hidden` set to renderer-owned display
 * state, keyed by `(symbol, timeframe)` and persisted in
 * `localStorage['ma.layerVisibility']` — the same species as the ADR-0077
 * `ma.userOverlays` store and the ADR-0039 `ma.*` convention (no sidecar, no
 * wire). The persisted model is, per bucket, the list of HIDDEN layer ids
 * (visible is the default); the in-memory shape the chart consumes stays a
 * `ReadonlySet<string>`, so `useCandleMarkerGroups` / `buildChartLayers` and the
 * series-visibility effects are unaffected.
 *
 * A bucket with no stored entry falls back to `DEFAULT_HIDDEN` — the "Clean on
 * open" default (ADR-0089): the always-on OBV strip is off, so a freshly-opened
 * symbol (no overlays, no scans) renders candles + volume only. An explicit
 * empty set (user turned OBV back on) is distinct from "never stored" and is
 * kept. The store is bounded (max buckets) so per-`(symbol,timeframe)`
 * persistence can't grow without limit.
 */
import { MARKET_STRUCTURE_LAYER_ID, OBV_LAYER_ID } from './chartSeries'

const STORAGE_KEY = 'ma.layerVisibility'
const MAX_KEYS = 50
const MAX_PER_KEY = 64

/** Persisted model: hidden layer ids grouped by a `(symbol, timeframe)` bucket.
 * Object-key insertion order is the LRU order used for eviction. */
export type LayerVisibilityStore = Record<string, string[]>

/** The "Clean on open" default hidden set (ADR-0089): the always-on OBV strip
 * and the market-structure markers/badge are off. Overlays / patterns aren't
 * present on a fresh chart, so this yields candles + volume only; the explicit
 * Clean preset hides the rest when clutter has accumulated. Stable module
 * constant — never mutate. */
export const DEFAULT_HIDDEN: ReadonlySet<string> = new Set([
  OBV_LAYER_ID,
  MARKET_STRUCTURE_LAYER_ID,
])

type Listener = () => void
const listeners = new Set<Listener>()

let store: LayerVisibilityStore = load()

/** The bucket key for a symbol + timeframe (`|` can't appear in either). */
export function layerVisibilityStoreKey(symbol: string, timeframe: string): string {
  return `${symbol}|${timeframe}`
}

function sanitize(raw: unknown): LayerVisibilityStore {
  const result: LayerVisibilityStore = {}
  if (typeof raw !== 'object' || raw === null) return result
  const obj = raw as Record<string, unknown>
  let keyCount = 0
  for (const bucketKey of Object.keys(obj)) {
    if (keyCount >= MAX_KEYS) break
    const list = obj[bucketKey]
    if (!Array.isArray(list)) continue
    const ids = [...new Set(list.filter((v): v is string => typeof v === 'string'))].slice(
      0,
      MAX_PER_KEY,
    )
    // Empty arrays are meaningful ("explicitly all visible") — kept.
    result[bucketKey] = ids
    keyCount += 1
  }
  return result
}

function load(): LayerVisibilityStore {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === null) return {}
    return sanitize(JSON.parse(stored) as unknown)
  } catch {
    return {}
  }
}

function persist(): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(store))
  } catch {
    /* localStorage blocked → in-memory copy is the session-only source of truth */
  }
}

function notify(): void {
  for (const listener of listeners) listener()
}

/** Snapshot for `useSyncExternalStore` — a stable reference replaced only on a
 * mutation, so React's snapshot equality holds between renders. */
export function getLayerVisibilitySnapshot(): LayerVisibilityStore {
  return store
}

/** Subscribe to any visibility mutation. Returns an unsubscribe function. */
export function subscribeLayerVisibility(callback: Listener): () => void {
  listeners.add(callback)
  return () => {
    listeners.delete(callback)
  }
}

/** The hidden set for a bucket: the stored ids, or `DEFAULT_HIDDEN` when the
 * bucket was never stored. Returns a fresh `Set` for stored buckets so callers
 * may not mutate the persisted array. */
export function hiddenForBucket(
  snapshot: LayerVisibilityStore,
  symbol: string,
  timeframe: string,
): ReadonlySet<string> {
  const stored = snapshot[layerVisibilityStoreKey(symbol, timeframe)]
  return stored !== undefined ? new Set(stored) : DEFAULT_HIDDEN
}

function writeBucket(bucketKey: string, ids: string[]): void {
  const next: LayerVisibilityStore = { ...store }
  // Re-insert the bucket last so it becomes the most-recently-used (LRU).
  delete next[bucketKey]
  next[bucketKey] = ids.slice(0, MAX_PER_KEY)
  const keys = Object.keys(next)
  if (keys.length > MAX_KEYS) {
    for (const stale of keys.slice(0, keys.length - MAX_KEYS)) delete next[stale]
  }
  store = next
  persist()
  notify()
}

/** Replace a bucket's hidden set (used by preset application), persist, notify. */
export function setLayerVisibility(
  symbol: string,
  timeframe: string,
  hidden: ReadonlySet<string>,
): void {
  writeBucket(layerVisibilityStoreKey(symbol, timeframe), [...hidden])
}

/** Toggle one layer id in a bucket's hidden set, seeding from `DEFAULT_HIDDEN`
 * when the bucket was never stored, then persist + notify. */
export function toggleLayerVisibility(symbol: string, timeframe: string, id: string): void {
  const bucketKey = layerVisibilityStoreKey(symbol, timeframe)
  const current = store[bucketKey] ?? [...DEFAULT_HIDDEN]
  const next = new Set(current)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  writeBucket(bucketKey, [...next])
}
