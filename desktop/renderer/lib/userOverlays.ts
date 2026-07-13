/**
 * User-originated overlay store (Plan 0082 phase 3, ADR-0077).
 *
 * A renderer-owned layer of indicator overlays the *user* added from the UI,
 * keyed by `(symbol, timeframe)` and persisted in `localStorage['ma.userOverlays']`
 * (extending ADR-0039's presentation-pref convention — no sidecar, no MCP call,
 * never on the wire). Shaped like `theme.ts` / `chartStyle.ts`: a module-level
 * in-memory source of truth hydrated once from storage, mutators that write
 * through and notify subscribers, and every `localStorage` access wrapped so a
 * blocked/privacy context degrades to session-only rather than throwing.
 *
 * These merge with the agent's overlays for drawing (`mergeOverlays`), deduped by
 * `overlayKey`, with the user layer STICKY — an agent `chart.show`/`chart.update`
 * replaces only the agent overlays (a prop), never this store. `mergeOverlays`
 * also reports which drawn keys are user-originated so the legend (phase 4) can
 * offer remove for those and hide-only for the agent's.
 *
 * Scope guard: only the client-computable indicator kinds are ever stored
 * (`USER_OVERLAY_KINDS`); `price_line` carries agent analysis semantics and is
 * never a user overlay (ADR-0077). The store is bounded (max keys, max per key)
 * so per-`(symbol,timeframe)` persistence can't grow without limit.
 */
import { overlayKey } from './chartSeries'
import type { OverlayKind, OverlaySpec } from '../types/events'

/** The indicator kinds a user may add as an overlay — the client-computable set
 * whose render path exists (ema/sma/bbands/supertrend/ichimoku). `price_line`
 * (agent analysis), `rsi`/`macd` (no draw path) are excluded by omission. */
export const USER_OVERLAY_KINDS: readonly OverlayKind[] = [
  'ema',
  'sma',
  'bbands',
  'supertrend',
  'ichimoku',
  // Plan 0091 momentum oscillators — user-addable, each in its own sub-pane.
  'stochastic',
  'stoch_rsi',
  'cci',
  'williams_r',
  'roc',
  // Plan 0091 money-flow — user-addable, each in its own sub-pane.
  'mfi',
  'cmf',
  'ad_line',
]

const STORAGE_KEY = 'ma.userOverlays'
/** Bounds so per-`(symbol,timeframe)` persistence can't grow without limit
 * (ADR-0077): cap distinct keys and overlays-per-key; oldest key is evicted. */
const MAX_KEYS = 50
const MAX_PER_KEY = 12

/** Persisted model: user overlays grouped by a `(symbol, timeframe)` bucket key.
 * Insertion order of the object keys is the LRU order used for eviction. */
export type UserOverlaysStore = Record<string, OverlaySpec[]>

type Listener = () => void
const listeners = new Set<Listener>()

let store: UserOverlaysStore = load()

/** The bucket key for a symbol + timeframe. `|` can't appear in either, so
 * the join is unambiguous. */
export function userOverlayStoreKey(symbol: string, timeframe: string): string {
  return `${symbol}|${timeframe}`
}

function isUserOverlayKind(kind: unknown): kind is OverlayKind {
  return typeof kind === 'string' && (USER_OVERLAY_KINDS as readonly string[]).includes(kind)
}

/** Coerce an unknown parsed value into a clean `OverlaySpec` for the user layer,
 * or `null` if it isn't a storable indicator overlay. Keeps only the numeric
 * indicator fields — never `price`/`label`/`role` (those are agent-only). */
function sanitizeSpec(raw: unknown): OverlaySpec | null {
  if (typeof raw !== 'object' || raw === null) return null
  const obj = raw as Record<string, unknown>
  if (!isUserOverlayKind(obj.kind)) return null
  const num = (value: unknown): number | undefined =>
    typeof value === 'number' && Number.isFinite(value) ? value : undefined
  const spec: OverlaySpec = { kind: obj.kind }
  const period = num(obj.period)
  if (period !== undefined) spec.period = period
  const multiplier = num(obj.multiplier)
  if (multiplier !== undefined) spec.multiplier = multiplier
  const conversion = num(obj.conversion)
  if (conversion !== undefined) spec.conversion = conversion
  const base = num(obj.base)
  if (base !== undefined) spec.base = base
  const spanB = num(obj.span_b)
  if (spanB !== undefined) spec.span_b = spanB
  const displacement = num(obj.displacement)
  if (displacement !== undefined) spec.displacement = displacement
  return spec
}

/** Coerce an unknown parsed store into a well-formed, deduped, bounded map. */
function sanitize(raw: unknown): UserOverlaysStore {
  const result: UserOverlaysStore = {}
  if (typeof raw !== 'object' || raw === null) return result
  const obj = raw as Record<string, unknown>
  let keyCount = 0
  for (const bucketKey of Object.keys(obj)) {
    if (keyCount >= MAX_KEYS) break
    const list = obj[bucketKey]
    if (!Array.isArray(list)) continue
    const specs = dedupeByKey(
      list.map(sanitizeSpec).filter((s): s is OverlaySpec => s !== null),
    ).slice(0, MAX_PER_KEY)
    if (specs.length > 0) {
      result[bucketKey] = specs
      keyCount += 1
    }
  }
  return result
}

/** Dedupe a list of overlays by `overlayKey`, latest occurrence winning (so a
 * re-add with a changed parameter for the same kind+period replaces the prior). */
function dedupeByKey(specs: OverlaySpec[]): OverlaySpec[] {
  const byKey = new Map<string, OverlaySpec>()
  for (const spec of specs) byKey.set(overlayKey(spec), spec)
  return [...byKey.values()]
}

function load(): UserOverlaysStore {
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
export function getUserOverlaysSnapshot(): UserOverlaysStore {
  return store
}

/** The user overlays for one `(symbol, timeframe)` (a stable empty array when the
 * bucket is absent — do not mutate the returned array). */
export function loadUserOverlays(symbol: string, timeframe: string): OverlaySpec[] {
  return store[userOverlayStoreKey(symbol, timeframe)] ?? EMPTY
}
const EMPTY: OverlaySpec[] = []

/**
 * Add (or replace, by `overlayKey`) a user overlay for a `(symbol, timeframe)`,
 * persist, and notify. A non-storable kind (`price_line`, an unknown kind) is
 * ignored. The bucket is capped at `MAX_PER_KEY` (oldest dropped) and the store
 * at `MAX_KEYS` distinct buckets (oldest bucket evicted).
 */
export function addUserOverlay(symbol: string, timeframe: string, spec: OverlaySpec): void {
  const clean = sanitizeSpec(spec)
  if (clean === null) return
  const bucketKey = userOverlayStoreKey(symbol, timeframe)
  const existing = store[bucketKey] ?? []
  const merged = dedupeByKey([...existing, clean]).slice(-MAX_PER_KEY)

  const next: UserOverlaysStore = { ...store }
  // Re-insert the bucket last so it becomes the most-recently-used (LRU eviction).
  delete next[bucketKey]
  next[bucketKey] = merged

  const keys = Object.keys(next)
  if (keys.length > MAX_KEYS) {
    for (const stale of keys.slice(0, keys.length - MAX_KEYS)) delete next[stale]
  }
  store = next
  persist()
  notify()
}

/**
 * Remove a user overlay (matched by `overlayKey`) from a `(symbol, timeframe)`,
 * persist, and notify. An emptied bucket is dropped so the store doesn't retain
 * empty keys. A no-op if the bucket or overlay isn't present.
 */
export function removeUserOverlay(symbol: string, timeframe: string, spec: OverlaySpec): void {
  const bucketKey = userOverlayStoreKey(symbol, timeframe)
  const existing = store[bucketKey]
  if (existing === undefined) return
  const targetKey = overlayKey(spec)
  const remaining = existing.filter((s) => overlayKey(s) !== targetKey)
  if (remaining.length === existing.length) return
  const next: UserOverlaysStore = { ...store }
  if (remaining.length === 0) delete next[bucketKey]
  else next[bucketKey] = remaining
  store = next
  persist()
  notify()
}

/** Subscribe to any user-overlay mutation. Returns an unsubscribe function. */
export function subscribeUserOverlays(callback: Listener): () => void {
  listeners.add(callback)
  return () => {
    listeners.delete(callback)
  }
}

/** The effective overlay set drawn on the chart: the agent's overlays plus the
 * user's, deduped by `overlayKey` (an identical agent + user spec collapses to
 * one drawn series). `userKeys` is the `overlayKey` set present in the user layer
 * — the legend (phase 4) offers remove for those keys and hide-only for the rest.
 * Agent overlays come first (and win a key collision, since they're identical);
 * user-only overlays are appended. Never mutates its inputs. */
export function mergeOverlays(
  agent: ReadonlyArray<OverlaySpec> | undefined,
  user: ReadonlyArray<OverlaySpec>,
): { overlays: OverlaySpec[]; userKeys: Set<string> } {
  const userKeys = new Set(user.map(overlayKey))
  const seen = new Set<string>()
  const overlays: OverlaySpec[] = []
  for (const spec of agent ?? []) {
    const key = overlayKey(spec)
    if (seen.has(key)) continue
    seen.add(key)
    overlays.push(spec)
  }
  for (const spec of user) {
    const key = overlayKey(spec)
    if (seen.has(key)) continue
    seen.add(key)
    overlays.push(spec)
  }
  return { overlays, userKeys }
}
