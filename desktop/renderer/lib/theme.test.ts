/**
 * Theme preference unit tests (Plan 0033 phase 1, ADR-0039).
 *
 * jsdom provides localStorage + documentElement but NOT matchMedia, so the
 * system-follow paths use a controllable mock that can flip the OS preference
 * and fire `change` to subscribers.
 */
import { applyTheme, getStoredTheme, resolveEffective, setTheme, subscribeEffective } from './theme'

/** Install a controllable `prefers-color-scheme: dark` mock. Returns a handle
 * to flip the OS preference and notify every registered `change` listener. */
function installMatchMedia(initialDark: boolean): { setOsDark: (dark: boolean) => void } {
  const state = { matches: initialDark }
  const listeners = new Set<(e: MediaQueryListEvent) => void>()
  const mql = {
    get matches() {
      return state.matches
    },
    media: '(prefers-color-scheme: dark)',
    onchange: null,
    addEventListener: (_type: string, cb: (e: MediaQueryListEvent) => void) => {
      listeners.add(cb)
    },
    removeEventListener: (_type: string, cb: (e: MediaQueryListEvent) => void) => {
      listeners.delete(cb)
    },
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => true,
  }
  window.matchMedia = jest.fn().mockReturnValue(mql) as unknown as typeof window.matchMedia
  return {
    setOsDark: (dark: boolean) => {
      state.matches = dark
      for (const cb of listeners) cb({ matches: dark } as MediaQueryListEvent)
    },
  }
}

beforeEach(() => {
  window.localStorage.clear()
  delete document.documentElement.dataset.theme
})

describe('getStoredTheme', () => {
  it('defaults to system when no preference is stored', () => {
    expect(getStoredTheme()).toBe('system')
  })

  it('reads a stored explicit preference', () => {
    window.localStorage.setItem('ma.theme', 'dark')
    expect(getStoredTheme()).toBe('dark')
  })

  it('falls back to system for a malformed stored value', () => {
    window.localStorage.setItem('ma.theme', 'banana')
    expect(getStoredTheme()).toBe('system')
  })
})

describe('setTheme / applyTheme', () => {
  it('setTheme("dark") persists to localStorage AND sets data-theme', () => {
    setTheme('dark')
    expect(window.localStorage.getItem('ma.theme')).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  it('setTheme("light") persists and applies', () => {
    setTheme('light')
    expect(window.localStorage.getItem('ma.theme')).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('setTheme("system") removes the attribute and clears storage', () => {
    setTheme('dark')
    setTheme('system')
    expect(document.documentElement.dataset.theme).toBeUndefined()
    expect(window.localStorage.getItem('ma.theme')).toBeNull()
    expect(getStoredTheme()).toBe('system')
  })

  it('applyTheme is DOM-only and does not touch storage', () => {
    applyTheme('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(window.localStorage.getItem('ma.theme')).toBeNull()
    applyTheme('system')
    expect(document.documentElement.dataset.theme).toBeUndefined()
  })
})

describe('resolveEffective', () => {
  it('returns an explicit preference unchanged', () => {
    expect(resolveEffective('dark')).toBe('dark')
    expect(resolveEffective('light')).toBe('light')
  })

  it('resolves system to dark when the OS prefers dark', () => {
    installMatchMedia(true)
    expect(resolveEffective('system')).toBe('dark')
  })

  it('resolves system to light when the OS prefers light', () => {
    installMatchMedia(false)
    expect(resolveEffective('system')).toBe('light')
  })
})

describe('subscribeEffective', () => {
  it('fires on an OS-preference change while in system mode', () => {
    const os = installMatchMedia(false)
    const cb = jest.fn()
    const unsub = subscribeEffective(cb)
    os.setOsDark(true)
    expect(cb).toHaveBeenCalledTimes(1)
    expect(cb).toHaveBeenCalledWith('dark')
    unsub()
  })

  it('does NOT fire on an OS change while an explicit theme is set', () => {
    const os = installMatchMedia(false)
    setTheme('light') // explicit choice pins the theme; subscribe AFTER so the
    // setTheme notification doesn't count
    const cb = jest.fn()
    const unsub = subscribeEffective(cb)
    os.setOsDark(true)
    expect(cb).not.toHaveBeenCalled()
    unsub()
  })

  it('fires on an explicit setTheme regardless of OS preference', () => {
    installMatchMedia(false)
    const cb = jest.fn()
    const unsub = subscribeEffective(cb)
    setTheme('dark')
    expect(cb).toHaveBeenCalledWith('dark')
    unsub()
  })

  it('stops firing after unsubscribe', () => {
    const os = installMatchMedia(false)
    const cb = jest.fn()
    const unsub = subscribeEffective(cb)
    unsub()
    os.setOsDark(true)
    setTheme('dark')
    expect(cb).not.toHaveBeenCalled()
  })
})
