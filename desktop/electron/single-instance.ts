/**
 * Single-instance enforcement (Plan 0014, ADR-0021). Agent mode is sidecar-
 * resident state — a single toggle applies to every viewer — so two viewers
 * would create write contention and ambiguous UX. This reverses Plan 0007's
 * "two viewers OK" allowance.
 *
 * Extracted from `main.ts` (which has top-level side effects and isn't unit-
 * testable) into a function with an injectable `app`, mirroring how
 * `attachOrSpawnSidecar` is factored out of the bootstrap. The caller branches
 * on the boolean: `true` → proceed to create the window; `false` → a second
 * instance is launching, so quit and do nothing else.
 */
import type { BrowserWindow } from 'electron'

/** The narrow slice of Electron's `app` this needs — keeps the unit test from
 * having to stub the whole `App` surface. */
export interface SingleInstanceApp {
  requestSingleInstanceLock(): boolean
  quit(): void
  on(event: 'second-instance', listener: () => void): unknown
}

/**
 * Acquire the single-instance lock. Returns `true` if this is the primary
 * instance (caller proceeds), `false` if another instance already holds the
 * lock (caller must not create a window — we've requested quit).
 *
 * On `second-instance` (a duplicate launch), focuses the existing window so the
 * user's gesture is acknowledged rather than silently dropped.
 */
export function enforceSingleInstance(
  app: SingleInstanceApp,
  getMainWindow: () => BrowserWindow | null,
): boolean {
  const gotLock = app.requestSingleInstanceLock()
  if (!gotLock) {
    app.quit()
    return false
  }
  app.on('second-instance', () => {
    const window = getMainWindow()
    if (window && !window.isDestroyed()) {
      if (window.isMinimized()) window.restore()
      window.focus()
    }
  })
  return true
}
