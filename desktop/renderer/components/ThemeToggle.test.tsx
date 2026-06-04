/**
 * Plan 0033 phase 3 done-when: the header ThemeToggle cycles the preference
 * through System → Light → Dark and drives `lib/theme.ts` (localStorage +
 * `html[data-theme]`). Sharing the same `theme.ts` store as the Settings
 * control is covered structurally by both using `useThemePref`.
 *
 * jsdom provides localStorage + documentElement but NOT matchMedia; the
 * subscription path tolerates its absence (theme.ts try/catches), but we mock
 * it for parity with the real environment.
 */
import '@testing-library/jest-dom'

import { fireEvent, render, screen } from '@testing-library/react'

import { ThemeToggle } from './ThemeToggle'

function installMatchMedia(initialDark: boolean): void {
  const state = { matches: initialDark }
  window.matchMedia = jest.fn().mockReturnValue({
    get matches() {
      return state.matches
    },
    media: '(prefers-color-scheme: dark)',
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => true,
  }) as unknown as typeof window.matchMedia
}

beforeEach(() => {
  window.localStorage.clear()
  delete document.documentElement.dataset.theme
  installMatchMedia(false)
})

it('defaults to System when no preference is stored', () => {
  render(<ThemeToggle />)
  const button = screen.getByTestId('theme-toggle')
  expect(button).toHaveAttribute('data-theme-pref', 'system')
  expect(button).toHaveTextContent('System')
})

it('cycles System → Light → Dark → System, driving localStorage and data-theme', () => {
  render(<ThemeToggle />)
  const button = screen.getByTestId('theme-toggle')

  // System → Light
  fireEvent.click(button)
  expect(button).toHaveAttribute('data-theme-pref', 'light')
  expect(button).toHaveTextContent('Light')
  expect(window.localStorage.getItem('ma.theme')).toBe('light')
  expect(document.documentElement.dataset.theme).toBe('light')

  // Light → Dark
  fireEvent.click(button)
  expect(button).toHaveAttribute('data-theme-pref', 'dark')
  expect(window.localStorage.getItem('ma.theme')).toBe('dark')
  expect(document.documentElement.dataset.theme).toBe('dark')

  // Dark → System (attribute removed, storage cleared)
  fireEvent.click(button)
  expect(button).toHaveAttribute('data-theme-pref', 'system')
  expect(window.localStorage.getItem('ma.theme')).toBeNull()
  expect(document.documentElement.dataset.theme).toBeUndefined()
})

it('announces the current choice and the next one via aria-label', () => {
  render(<ThemeToggle />)
  const button = screen.getByTestId('theme-toggle')
  expect(button).toHaveAttribute('aria-label', 'Theme: System. Activate to switch to Light.')
  fireEvent.click(button)
  expect(button).toHaveAttribute('aria-label', 'Theme: Light. Activate to switch to Dark.')
})

it('reflects an already-stored preference on mount', () => {
  window.localStorage.setItem('ma.theme', 'dark')
  render(<ThemeToggle />)
  const button = screen.getByTestId('theme-toggle')
  expect(button).toHaveAttribute('data-theme-pref', 'dark')
  expect(button).toHaveTextContent('Dark')
})
