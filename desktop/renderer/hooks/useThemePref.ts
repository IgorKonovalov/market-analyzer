/**
 * React binding for the theme preference owned by `lib/theme.ts` (Plan 0033
 * phase 3). `theme.ts` is the single source of truth; this hook lets more than
 * one control — the header `ThemeToggle` and the Settings *Appearance*
 * segmented control — read and set the preference and stay in sync. `setTheme`
 * notifies every subscriber, so changing the theme in one control re-renders
 * the other.
 *
 * Implemented with `useSyncExternalStore` over `theme.ts`'s subscription: the
 * snapshot is the stored preference, and `subscribeEffective` fires on every
 * explicit `setTheme(...)` (and on an OS flip while in `system` mode, which
 * leaves the preference — and thus the snapshot — unchanged, so no spurious
 * re-render). `getStoredTheme` returns a primitive, so `Object.is` snapshot
 * comparison is stable.
 */
import { useCallback, useSyncExternalStore } from 'react'

import { getStoredTheme, setTheme, subscribeEffective, type ThemePref } from '../lib/theme'

export function useThemePref(): readonly [ThemePref, (pref: ThemePref) => void] {
  const pref = useSyncExternalStore<ThemePref>(subscribeEffective, getStoredTheme, () => 'system')
  const set = useCallback((next: ThemePref): void => {
    setTheme(next)
  }, [])
  return [pref, set] as const
}
