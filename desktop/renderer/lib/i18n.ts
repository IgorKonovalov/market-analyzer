/**
 * In-house, zero-dependency i18n (Plan 0069 phase 1, ADR-0063).
 *
 * The renderer owns localization — the sidecar stays English-authoritative and
 * negotiates no locale. This module is the single source of truth for the
 * active locale (mirroring `lib/theme.ts`) and the `t()` resolver over the
 * typed `en`/`ru` catalogs. Pluralization uses native `Intl.PluralRules` (three
 * categories for Russian); number/date/currency formatting stays `en-US` by
 * decision (ADR-0063), so `#` count substitutions and `format.ts` both format
 * `en-US` regardless of locale.
 *
 * Persistence is `localStorage['ma.locale']`; `en` is the default and — per
 * ADR-0063 — the test-suite locale, so existing renderer specs stay green.
 * Application sets `<html lang>` (screen-reader + `Intl` correctness). Unlike
 * the theme, the locale needs no pre-paint bootstrap: text is React-rendered,
 * not CSS-driven, so there is no flash to prevent.
 *
 * Every `localStorage` access is wrapped in try/catch: in a sandboxed or
 * privacy context it can throw, and the contract is to degrade to `en` rather
 * than crash the app.
 */
import { en } from '../locales/en'

export type Locale = 'en' | 'ru'
export type Params = Record<string, string | number>
/** A flat catalog: dotted key → message template (with optional `{param}` /
 * ICU-lite `{count, plural, …}` placeholders). */
export type Catalog = Record<string, string>

const STORAGE_KEY = 'ma.locale'
const DEFAULT_LOCALE: Locale = 'en'

// The `ru` catalog is authored in phase 6; until then only `en` is present and
// every lookup falls back to it. `t()` still selects Russian plural categories
// via `Intl.PluralRules` when the active locale is `ru`, so the resolver is
// exercised end-to-end before the catalog exists.
const CATALOGS: Partial<Record<Locale, Catalog>> = { en }

type Listener = (locale: Locale) => void

// Subscribers that re-render (or re-read `t()` output) when the locale changes.
// `useLocalePref` is the canonical consumer, mirroring `useThemePref`.
const listeners = new Set<Listener>()

/** Read the persisted locale. `en` when unset, malformed, or blocked. */
export function getStoredLocale(): Locale {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY)
    if (value === 'en' || value === 'ru') return value
  } catch {
    /* localStorage blocked → fall back to the default locale */
  }
  return DEFAULT_LOCALE
}

/** Apply a locale to the DOM only (no persistence): sets `<html lang>`. */
export function applyLocale(locale: Locale): void {
  try {
    document.documentElement.lang = locale
  } catch {
    /* no document (non-DOM context) → nothing to apply */
  }
}

/** Persist the locale, apply it to the DOM, and notify subscribers. */
export function setLocale(locale: Locale): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, locale)
  } catch {
    /* localStorage blocked → choice is session-only, applied below */
  }
  applyLocale(locale)
  for (const listener of listeners) listener(locale)
}

/**
 * Subscribe to locale changes. Fires on every `setLocale(...)`. Returns an
 * unsubscribe function; call it on unmount. The callback signature carries the
 * new locale, but `useSyncExternalStore` passes an arg-less callback — both are
 * compatible because the extra arg is simply ignored (as in `theme.ts`).
 */
export function subscribeLocale(callback: Listener): () => void {
  listeners.add(callback)
  return () => {
    listeners.delete(callback)
  }
}

// ── Resolver ────────────────────────────────────────────────────────────────

const warned = new Set<string>()

function isDev(): boolean {
  // Vite statically replaces `process.env.NODE_ENV` in the renderer bundle
  // ('production' in packaged builds); Jest sets it to 'test'. So warnings fire
  // in dev + test and are stripped in production.
  return process.env.NODE_ENV !== 'production'
}

/**
 * Translate `key` in the active locale, interpolating `params`. Falls back to
 * the `en` catalog per-key; a key absent from every catalog returns the key
 * string itself and logs a single dev-only warning (deduped per key).
 */
export function t(key: string, params?: Params): string {
  const locale = getStoredLocale()
  const template = lookup(locale, key)
  if (template === undefined) {
    if (isDev() && !warned.has(key)) {
      warned.add(key)
      console.warn(`[i18n] missing translation key: ${key}`)
    }
    return key
  }
  return formatMessage(template, params ?? {}, locale)
}

function lookup(locale: Locale, key: string): string | undefined {
  const primary = CATALOGS[locale]?.[key]
  if (primary !== undefined) return primary
  if (locale !== DEFAULT_LOCALE) return CATALOGS[DEFAULT_LOCALE]?.[key]
  return undefined
}

/**
 * Render a message template against `params` in `locale`. Pure and exported for
 * direct testing — `t()` is this plus catalog lookup and the missing-key path.
 *
 * Supports two placeholder forms:
 *   - `{name}` — interpolate `params.name`.
 *   - `{name, plural, one {…} few {…} many {…} other {…}}` — select an arm via
 *     `Intl.PluralRules(locale)`. Arm selectors are plural categories or exact
 *     `=N` matches; `#` inside an arm becomes the `en-US`-formatted count.
 */
export function formatMessage(template: string, params: Params, locale: Locale): string {
  let out = ''
  let i = 0
  while (i < template.length) {
    if (template[i] === '{') {
      const close = matchBrace(template, i)
      const inner = template.slice(i + 1, close)
      out += renderPlaceholder(inner, params, locale)
      i = close + 1
    } else {
      out += template[i]
      i++
    }
  }
  return out
}

/** Index of the `}` matching the `{` at `open`, accounting for nested braces. */
function matchBrace(s: string, open: number): number {
  let depth = 0
  for (let i = open; i < s.length; i++) {
    if (s[i] === '{') depth++
    else if (s[i] === '}') {
      depth--
      if (depth === 0) return i
    }
  }
  return s.length // unbalanced template → consume to the end (best effort)
}

const PLURAL_RE = /^(\w+)\s*,\s*plural\s*,\s*([\s\S]*)$/

function renderPlaceholder(inner: string, params: Params, locale: Locale): string {
  const plural = PLURAL_RE.exec(inner)
  if (plural) {
    const [, name, arms] = plural
    const count = Number(params[name] ?? 0)
    const category = new Intl.PluralRules(locale).select(count)
    const body = selectArm(arms, count, category)
    // `#` → the en-US-formatted count; then interpolate any nested placeholders.
    return formatMessage(body.replace(/#/g, formatCount(count)), params, locale)
  }
  const name = inner.trim()
  const value = params[name]
  return value === undefined ? `{${name}}` : String(value)
}

interface Arm {
  selector: string
  body: string
}

function selectArm(arms: string, count: number, category: Intl.LDMLPluralRule): string {
  const parsed = parseArms(arms)
  const exact = parsed.find((a) => a.selector === `=${count}`)
  if (exact) return exact.body
  const byCategory = parsed.find((a) => a.selector === category)
  if (byCategory) return byCategory.body
  return parsed.find((a) => a.selector === 'other')?.body ?? ''
}

/** Parse `=0 {…} one {…} other {…}` into ordered `{selector, body}` arms. */
function parseArms(arms: string): Arm[] {
  const result: Arm[] = []
  let i = 0
  while (i < arms.length) {
    while (i < arms.length && /\s/.test(arms[i])) i++
    if (i >= arms.length) break
    let selector = ''
    while (i < arms.length && arms[i] !== '{' && !/\s/.test(arms[i])) {
      selector += arms[i]
      i++
    }
    while (i < arms.length && /\s/.test(arms[i])) i++
    if (arms[i] !== '{') break // malformed arm list → stop parsing
    const close = matchBrace(arms, i)
    result.push({ selector, body: arms.slice(i + 1, close) })
    i = close + 1
  }
  return result
}

/** Format a plural count. `en-US` regardless of locale, per ADR-0063. */
function formatCount(count: number): string {
  return count.toLocaleString('en-US')
}
