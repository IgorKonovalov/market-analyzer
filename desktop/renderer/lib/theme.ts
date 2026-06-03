/**
 * Theme preference (Plan 0033 phase 1, ADR-0039).
 *
 * The renderer owns the theme — not the sidecar's `config.json` — because the
 * choice must be applied *before first paint* (no flash-of-wrong-theme) and must
 * keep working while the sidecar is attaching or down. Persistence is
 * `localStorage['ma.theme']`; application is a `data-theme` attribute on
 * `<html>` that CSS reads (explicit attribute wins; absent = follow the OS via
 * `prefers-color-scheme`). A tiny inline bootstrap in `index.html` sets the
 * attribute pre-bundle for the no-flash property; this module owns every
 * subsequent read/write and is the single source of truth the Settings control,
 * the header toggle, and the candlestick chart all share.
 *
 * Every `localStorage` / `matchMedia` access is wrapped in try/catch: in a
 * sandboxed or privacy context they can throw, and the contract is to degrade to
 * `system` (session-only) rather than crash the app.
 */
export type ThemePref = 'light' | 'dark' | 'system'
export type EffectiveTheme = 'light' | 'dark'

const STORAGE_KEY = 'ma.theme'
const DARK_QUERY = '(prefers-color-scheme: dark)'

type Listener = (effective: EffectiveTheme) => void

// Subscribers that want to react to *any* effective-theme change — both an
// explicit `setTheme(...)` and an OS-preference flip while in `system` mode.
// The candlestick chart (phase 4) is the canonical consumer: it re-reads its
// CSS-token colors when this fires.
const listeners = new Set<Listener>()

/** Read the persisted preference. `system` when unset, malformed, or blocked. */
export function getStoredTheme(): ThemePref {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY)
    if (value === 'light' || value === 'dark' || value === 'system') return value
  } catch {
    /* localStorage blocked → fall back to system */
  }
  return 'system'
}

/**
 * Apply a preference to the DOM only (no persistence). An explicit `light`/`dark`
 * sets `html[data-theme]`; `system` *removes* the attribute so the
 * `prefers-color-scheme` media query governs.
 */
export function applyTheme(pref: ThemePref): void {
  const root = document.documentElement
  if (pref === 'light' || pref === 'dark') {
    root.dataset.theme = pref
  } else {
    delete root.dataset.theme
  }
}

/** Persist the preference, apply it to the DOM, and notify subscribers. */
export function setTheme(pref: ThemePref): void {
  try {
    if (pref === 'system') {
      window.localStorage.removeItem(STORAGE_KEY)
    } else {
      window.localStorage.setItem(STORAGE_KEY, pref)
    }
  } catch {
    /* localStorage blocked → preference is session-only, applied below */
  }
  applyTheme(pref)
  const effective = resolveEffective(pref)
  for (const listener of listeners) listener(effective)
}

/** Resolve a preference to a concrete theme; `system` consults `matchMedia`. */
export function resolveEffective(pref: ThemePref): EffectiveTheme {
  if (pref === 'light' || pref === 'dark') return pref
  try {
    return window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

/**
 * Subscribe to effective-theme changes. Fires on an explicit `setTheme(...)`
 * (any caller) and on an OS-preference flip *only while the stored preference is
 * `system`* — an explicit choice pins the theme, so OS changes are ignored then.
 * Returns an unsubscribe function; call it on unmount.
 */
export function subscribeEffective(callback: Listener): () => void {
  listeners.add(callback)

  let mql: MediaQueryList | null = null
  const onOsChange = (): void => {
    if (getStoredTheme() === 'system') callback(resolveEffective('system'))
  }
  try {
    mql = window.matchMedia(DARK_QUERY)
    mql.addEventListener('change', onOsChange)
  } catch {
    /* matchMedia unavailable → no OS-follow updates; explicit changes still fire */
  }

  return () => {
    listeners.delete(callback)
    mql?.removeEventListener('change', onOsChange)
  }
}
