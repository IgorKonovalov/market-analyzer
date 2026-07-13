/**
 * Chart display presets (Plan 0096 phase 3, ADR-0089).
 *
 * A preset is a named, renderer-owned bundle of display state:
 * `{ overlays, show }` — the indicator overlays to draw (composed over the
 * ADR-0077 `ma.userOverlays` store) plus which non-overlay categories (OBV /
 * candlestick markers / trendlines / price lines) stay visible. Four built-ins
 * ship as code constants (Clean / Trend / Mean-reversion / Patterns); user-saved
 * presets persist globally in `localStorage['ma.chartPresets']` (the ADR-0039
 * `ma.*` convention, bounded/pruned like `ma.userOverlays`).
 *
 * Presets are GLOBAL (a reusable intent), applied — not pinned — into the current
 * `(symbol, timeframe)`'s sticky state: applying writes the overlays into the
 * user-overlay bucket and the resolved hidden set into the visibility bucket,
 * after which normal stickiness remembers any tweak. They never cross the wire,
 * issue no sidecar call, and never touch `candleType` (the ADR-0062 global pref).
 */
import { OBV_LAYER_ID } from './chartSeries'
import { overlayLayerId } from './overlays'
import type { ChartLayer } from '../components/LayersPanel'
import type { OverlaySpec } from '../types/events'

/** Which non-overlay layer categories a preset keeps visible. Overlay
 * visibility is implied by membership in `overlays` (a preset draws exactly its
 * overlays and hides the rest). */
export interface PresetShow {
  obv: boolean
  candlesticks: boolean
  trendlines: boolean
  priceLines: boolean
}

export interface ChartPreset {
  name: string
  overlays: OverlaySpec[]
  show: PresetShow
  /** Built-ins ship as code constants; user-saved presets persist. */
  builtIn: boolean
}

const NOTHING: PresetShow = {
  obv: false,
  candlesticks: false,
  trendlines: false,
  priceLines: false,
}

/** The default preset name — a chart with no prior sticky state reads as Clean. */
export const CLEAN_PRESET_NAME = 'Clean'

/** Built-in presets (code constants, not stored). Order is the selector order. */
export const BUILT_IN_PRESETS: readonly ChartPreset[] = [
  { name: CLEAN_PRESET_NAME, overlays: [], show: { ...NOTHING }, builtIn: true },
  {
    name: 'Trend',
    overlays: [
      { kind: 'ema', period: 20 },
      { kind: 'supertrend', period: 10 },
      { kind: 'ichimoku' },
    ],
    show: { ...NOTHING, priceLines: true },
    builtIn: true,
  },
  {
    name: 'Mean-reversion',
    overlays: [{ kind: 'bbands', period: 20, multiplier: 2 }, { kind: 'stochastic' }],
    show: { ...NOTHING },
    builtIn: true,
  },
  {
    name: 'Patterns',
    overlays: [],
    show: { obv: false, candlesticks: true, trendlines: true, priceLines: true },
    builtIn: true,
  },
]

/**
 * The hidden-id set to write when a preset is applied over the current layers.
 * An overlay is hidden unless the preset draws it (by `overlayLayerId`); each
 * non-overlay category is hidden unless its `show` flag is set. OBV's stable id
 * is seeded from `show.obv` even before its row exists (bars may arrive later).
 * Pure — the component supplies the live `layers`.
 */
export function hiddenForPreset(preset: ChartPreset, layers: ChartLayer[]): Set<string> {
  const keep = new Set(preset.overlays.map(overlayLayerId))
  const hidden = new Set<string>()
  for (const layer of layers) {
    switch (layer.kind) {
      case 'overlay':
        if (!keep.has(layer.id)) hidden.add(layer.id)
        break
      case 'series':
        if (!preset.show.obv) hidden.add(layer.id)
        break
      case 'marker':
        if (!preset.show.candlesticks) hidden.add(layer.id)
        break
      case 'trendline':
        if (!preset.show.trendlines) hidden.add(layer.id)
        break
      case 'price_line':
        if (!preset.show.priceLines) hidden.add(layer.id)
        break
      default:
        break
    }
  }
  if (!preset.show.obv) hidden.add(OBV_LAYER_ID)
  return hidden
}

// ── User-saved preset store (ma.chartPresets) ────────────────────────────────

const STORAGE_KEY = 'ma.chartPresets'
const MAX_USER_PRESETS = 30

type Listener = () => void
const listeners = new Set<Listener>()

let userPresets: ChartPreset[] = load()

function sanitizeShow(raw: unknown): PresetShow {
  const obj = (typeof raw === 'object' && raw !== null ? raw : {}) as Record<string, unknown>
  const flag = (v: unknown): boolean => v === true
  return {
    obv: flag(obj.obv),
    candlesticks: flag(obj.candlesticks),
    trendlines: flag(obj.trendlines),
    priceLines: flag(obj.priceLines),
  }
}

function sanitizeOverlays(raw: unknown): OverlaySpec[] {
  if (!Array.isArray(raw)) return []
  return raw.filter(
    (s): s is OverlaySpec =>
      typeof s === 'object' && s !== null && typeof (s as { kind?: unknown }).kind === 'string',
  )
}

function sanitize(raw: unknown): ChartPreset[] {
  if (!Array.isArray(raw)) return []
  const seen = new Set<string>()
  const result: ChartPreset[] = []
  for (const entry of raw) {
    if (typeof entry !== 'object' || entry === null) continue
    const obj = entry as Record<string, unknown>
    const name = typeof obj.name === 'string' ? obj.name.trim() : ''
    if (name === '' || seen.has(name)) continue
    seen.add(name)
    result.push({
      name,
      overlays: sanitizeOverlays(obj.overlays),
      show: sanitizeShow(obj.show),
      builtIn: false,
    })
    if (result.length >= MAX_USER_PRESETS) break
  }
  return result
}

function load(): ChartPreset[] {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === null) return []
    return sanitize(JSON.parse(stored) as unknown)
  } catch {
    return []
  }
}

function persist(): void {
  try {
    // builtIn is implied false for stored presets — don't serialize it.
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(userPresets.map(({ name, overlays, show }) => ({ name, overlays, show }))),
    )
  } catch {
    /* localStorage blocked → session-only */
  }
}

function notify(): void {
  for (const listener of listeners) listener()
}

/** Snapshot of the user-saved presets for `useSyncExternalStore`. */
export function getUserPresetsSnapshot(): ChartPreset[] {
  return userPresets
}

/** Subscribe to user-preset mutations. Returns an unsubscribe function. */
export function subscribeChartPresets(callback: Listener): () => void {
  listeners.add(callback)
  return () => {
    listeners.delete(callback)
  }
}

/** All presets in selector order: the built-ins followed by user-saved ones. */
export function allPresets(userSnapshot: ChartPreset[]): ChartPreset[] {
  return [...BUILT_IN_PRESETS, ...userSnapshot]
}

/**
 * Save (or replace, by name) a user preset capturing the current overlays +
 * category visibility, persist, and notify. A blank name is ignored; a name
 * colliding with a built-in is rejected so built-ins stay canonical. The store
 * is capped at `MAX_USER_PRESETS` (oldest dropped).
 */
export function saveCurrentAsPreset(name: string, overlays: OverlaySpec[], show: PresetShow): void {
  const clean = name.trim()
  if (clean === '') return
  if (BUILT_IN_PRESETS.some((p) => p.name === clean)) return
  const preset: ChartPreset = {
    name: clean,
    overlays: [...overlays],
    show: { ...show },
    builtIn: false,
  }
  const next = userPresets.filter((p) => p.name !== clean)
  next.push(preset)
  userPresets = next.slice(-MAX_USER_PRESETS)
  persist()
  notify()
}
