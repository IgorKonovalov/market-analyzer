/**
 * React binding for the locale preference owned by `lib/i18n.ts` (Plan 0069
 * phase 1). Mirrors `useThemePref`: `i18n.ts` is the single source of truth and
 * this hook lets any control read and set the locale and stay in sync.
 * `setLocale` notifies every subscriber, so switching the language in the
 * Settings control re-renders every `t()`-consuming view.
 *
 * Implemented with `useSyncExternalStore` over `i18n.ts`'s subscription: the
 * snapshot is the stored locale (a primitive, so `Object.is` comparison is
 * stable) and `subscribeLocale` fires on every explicit `setLocale(...)`.
 */
import { useCallback, useSyncExternalStore } from 'react'

import { getStoredLocale, setLocale, subscribeLocale, type Locale } from '../lib/i18n'

export function useLocalePref(): readonly [Locale, (locale: Locale) => void] {
  const locale = useLocale()
  const set = useCallback((next: Locale): void => {
    setLocale(next)
  }, [])
  return [locale, set] as const
}

/**
 * Read-only locale subscription. Mounted once at the App root so a `setLocale`
 * re-renders the whole tree and every `t()`-keyed surface re-localizes on the
 * spot — not only the controls that also need the setter (Plan 0069 phase 3).
 */
export function useLocale(): Locale {
  return useSyncExternalStore<Locale>(subscribeLocale, getStoredLocale, () => 'en')
}
