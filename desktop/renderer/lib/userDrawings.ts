/**
 * User-originated drawing store (Plan 0097 phase 2, ADR-0091).
 *
 * A renderer-owned layer of freeform drawings the *user* drew from the dock,
 * keyed by SYMBOL (not `(symbol, timeframe)` — a drawing is a claim about price
 * over time, anchored to `(time, price)` and shown on every timeframe, ADR-0091)
 * and persisted in `localStorage['ma.userDrawings']` (extending ADR-0039's
 * presentation-pref convention — no sidecar, no MCP call, never on the wire).
 *
 * Shaped like `userOverlays.ts` / `chartStyle.ts`: a module-level in-memory
 * source of truth hydrated once from storage, mutators that write through and
 * notify subscribers, every `localStorage` access wrapped so a blocked/privacy
 * context degrades to session-only rather than throwing. The store holds only
 * `provenance: "user"` specs; agent drawings arrive over the wire and are never
 * persisted (phase 4). Bounded (max symbols, max per symbol) so per-symbol
 * persistence can't grow without limit.
 */
import type { DrawingKind, DrawingSpec, DrawingStyle, TimePricePoint } from '../types/events'
import { POINT_COUNT_BY_KIND } from './drawings'

const STORAGE_KEY = 'ma.userDrawings'
/** Bounds so per-symbol persistence can't grow without limit (ADR-0091): cap
 * distinct symbols and drawings-per-symbol; the oldest symbol is evicted LRU. */
const MAX_SYMBOLS = 50
const MAX_PER_SYMBOL = 100

/** Persisted model: user drawings grouped by symbol. Object-key insertion order
 * is the LRU order used for eviction. */
export type UserDrawingsStore = Record<string, DrawingSpec[]>

type Listener = () => void
const listeners = new Set<Listener>()

let store: UserDrawingsStore = load()

function isDrawingKind(kind: unknown): kind is DrawingKind {
  return typeof kind === 'string' && kind in POINT_COUNT_BY_KIND
}

function sanitizePoint(raw: unknown): TimePricePoint | null {
  if (typeof raw !== 'object' || raw === null) return null
  const obj = raw as Record<string, unknown>
  if (typeof obj.ts !== 'string' || obj.ts.length === 0) return null
  if (typeof obj.price !== 'number' || !Number.isFinite(obj.price)) return null
  return { ts: obj.ts, price: obj.price }
}

function sanitizeStyle(raw: unknown): DrawingStyle | undefined {
  if (typeof raw !== 'object' || raw === null) return undefined
  const obj = raw as Record<string, unknown>
  const style: DrawingStyle = {}
  if (typeof obj.color === 'string') style.color = obj.color
  if (typeof obj.width === 'number' && Number.isFinite(obj.width) && obj.width > 0)
    style.width = obj.width
  return style.color === undefined && style.width === undefined ? undefined : style
}

/** Coerce an unknown parsed value into a clean user `DrawingSpec`, or `null` when
 * it isn't a well-formed drawing (bad kind, wrong anchor count, missing id). The
 * stored provenance is always forced to `"user"` — the store never holds agent
 * drawings. */
function sanitizeSpec(raw: unknown): DrawingSpec | null {
  if (typeof raw !== 'object' || raw === null) return null
  const obj = raw as Record<string, unknown>
  if (!isDrawingKind(obj.kind)) return null
  if (typeof obj.id !== 'string' || obj.id.length === 0) return null
  if (!Array.isArray(obj.points)) return null
  const points = obj.points.map(sanitizePoint).filter((p): p is TimePricePoint => p !== null)
  if (points.length !== POINT_COUNT_BY_KIND[obj.kind]) return null
  const spec: DrawingSpec = { kind: obj.kind, points, provenance: 'user', id: obj.id }
  const style = sanitizeStyle(obj.style)
  if (style !== undefined) spec.style = style
  return spec
}

/** Dedupe a list of drawings by `id`, latest occurrence winning (an edit re-adds
 * the same id with new geometry). */
function dedupeById(specs: DrawingSpec[]): DrawingSpec[] {
  const byId = new Map<string, DrawingSpec>()
  for (const spec of specs) byId.set(spec.id, spec)
  return [...byId.values()]
}

/** Coerce an unknown parsed store into a well-formed, deduped, bounded map. */
function sanitize(raw: unknown): UserDrawingsStore {
  const result: UserDrawingsStore = {}
  if (typeof raw !== 'object' || raw === null) return result
  const obj = raw as Record<string, unknown>
  let symbolCount = 0
  for (const symbol of Object.keys(obj)) {
    if (symbolCount >= MAX_SYMBOLS) break
    const list = obj[symbol]
    if (!Array.isArray(list)) continue
    const specs = dedupeById(
      list.map(sanitizeSpec).filter((s): s is DrawingSpec => s !== null),
    ).slice(0, MAX_PER_SYMBOL)
    if (specs.length > 0) {
      result[symbol] = specs
      symbolCount += 1
    }
  }
  return result
}

function load(): UserDrawingsStore {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === null) return {}
    return sanitize(JSON.parse(stored) as unknown)
  } catch {
    /* localStorage blocked or JSON malformed → empty, session-only */
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
export function getUserDrawingsSnapshot(): UserDrawingsStore {
  return store
}

const EMPTY: DrawingSpec[] = []

/** The user drawings for one symbol (a stable empty array when absent — do not
 * mutate the returned array). */
export function loadUserDrawings(symbol: string): DrawingSpec[] {
  return store[symbol] ?? EMPTY
}

/** Touch `symbol` as most-recently-used and evict the oldest symbol beyond the
 * cap. Returns the next store map (caller assigns + persists + notifies). */
function withBucket(symbol: string, specs: DrawingSpec[]): UserDrawingsStore {
  const next: UserDrawingsStore = { ...store }
  // Re-insert last so it becomes most-recently-used (LRU eviction order).
  delete next[symbol]
  if (specs.length > 0) next[symbol] = specs.slice(0, MAX_PER_SYMBOL)
  const keys = Object.keys(next)
  if (keys.length > MAX_SYMBOLS) {
    for (const stale of keys.slice(0, keys.length - MAX_SYMBOLS)) delete next[stale]
  }
  return next
}

/**
 * Add (or replace, by `id`) a user drawing for a symbol, persist, and notify. A
 * malformed spec is ignored. The bucket is capped at `MAX_PER_SYMBOL` (oldest
 * dropped) and the store at `MAX_SYMBOLS` symbols (oldest evicted).
 */
export function addUserDrawing(symbol: string, spec: DrawingSpec): void {
  const clean = sanitizeSpec(spec)
  if (clean === null) return
  const existing = store[symbol] ?? []
  store = withBucket(symbol, dedupeById([...existing, clean]))
  persist()
  notify()
}

/**
 * Replace a user drawing's geometry (matched by `id`) for a symbol — the drag /
 * re-anchor path. A no-op when the id isn't present (never resurrects a deleted
 * drawing). Persists + notifies on a real change.
 */
export function updateUserDrawing(symbol: string, spec: DrawingSpec): void {
  const clean = sanitizeSpec(spec)
  if (clean === null) return
  const existing = store[symbol]
  if (existing === undefined || !existing.some((s) => s.id === clean.id)) return
  store = withBucket(
    symbol,
    existing.map((s) => (s.id === clean.id ? clean : s)),
  )
  persist()
  notify()
}

/**
 * Remove a user drawing (by `id`) from a symbol, persist, and notify. An emptied
 * bucket is dropped. A no-op if the drawing isn't present.
 */
export function removeUserDrawing(symbol: string, id: string): void {
  const existing = store[symbol]
  if (existing === undefined) return
  const remaining = existing.filter((s) => s.id !== id)
  if (remaining.length === existing.length) return
  store = withBucket(symbol, remaining)
  persist()
  notify()
}

/** Subscribe to any user-drawing mutation. Returns an unsubscribe function. */
export function subscribeUserDrawings(callback: Listener): () => void {
  listeners.add(callback)
  return () => {
    listeners.delete(callback)
  }
}
