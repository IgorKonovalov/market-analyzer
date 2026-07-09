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
  const locale = useSyncExternalStore<Locale>(subscribeLocale, getStoredLocale, () => 'en')
  const set = useCallback((next: Locale): void => {
    setLocale(next)
  }, [])
  return [locale, set] as const
}
