/**
 * Compact theme toggle pinned to the app header (Plan 0033 phase 3, ADR-0039).
 * One-click cycling through System → Light → Dark → System; the current
 * preference is shown as a geometric glyph + label. It shares `lib/theme.ts`
 * (via `useThemePref`) with the Settings *Appearance* control, so the two stay
 * in sync. Rendered as a plain `<button>` — keyboard-operable for free; the
 * `aria-label` announces the current choice and what activation will switch to.
 *
 * Geometric glyphs (○ ● ◐) are used rather than emoji so they always render as
 * text in both themes, never as a color emoji the palette can't follow.
 */
import { useCallback } from 'react'

import { useThemePref } from '../hooks/useThemePref'
import { t } from '../lib/i18n'
import type { ThemePref } from '../lib/theme'
import styles from './ThemeToggle.module.css'

const ORDER: readonly ThemePref[] = ['system', 'light', 'dark']
const LABEL: Record<ThemePref, string> = {
  system: 'themeToggle.system',
  light: 'themeToggle.light',
  dark: 'themeToggle.dark',
}
const GLYPH: Record<ThemePref, string> = { system: '◐', light: '○', dark: '●' }

export function ThemeToggle(): JSX.Element {
  const [pref, setPref] = useThemePref()
  const next = ORDER[(ORDER.indexOf(pref) + 1) % ORDER.length]

  const onClick = useCallback((): void => {
    setPref(next)
  }, [setPref, next])

  const currentLabel = t(LABEL[pref])
  const nextLabel = t(LABEL[next])

  return (
    <button
      type="button"
      className={styles.toggle}
      data-testid="theme-toggle"
      data-theme-pref={pref}
      aria-label={t('themeToggle.ariaLabel', { current: currentLabel, next: nextLabel })}
      title={t('themeToggle.title', { current: currentLabel })}
      onClick={onClick}
    >
      <span className={styles.glyph} aria-hidden="true">
        {GLYPH[pref]}
      </span>
      <span className={styles.label}>{currentLabel}</span>
    </button>
  )
}
