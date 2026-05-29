/**
 * Plan 0014 phase 3 done-when: single-instance enforcement (ADR-0021).
 *
 * Defends (via an injectable `app` double, not a real Electron app):
 *   - requestSingleInstanceLock() is called exactly once on boot.
 *   - lock acquired (true) → returns true (caller proceeds to create a window)
 *     and registers the second-instance handler.
 *   - lock NOT acquired (false) → calls app.quit() and returns false (caller
 *     creates no window).
 *   - a second-instance event focuses the existing window (restoring it first
 *     if minimised).
 */
import type { BrowserWindow } from 'electron'

import { enforceSingleInstance, type SingleInstanceApp } from '../../electron/single-instance'

interface FakeApp extends SingleInstanceApp {
  requestSingleInstanceLock: jest.Mock<boolean, []>
  quit: jest.Mock<void, []>
  on: jest.Mock
  emitSecondInstance: () => void
}

function makeApp(gotLock: boolean): FakeApp {
  let secondInstanceListener: (() => void) | null = null
  const app: FakeApp = {
    requestSingleInstanceLock: jest.fn(() => gotLock),
    quit: jest.fn(),
    on: jest.fn((event: string, listener: () => void) => {
      if (event === 'second-instance') secondInstanceListener = listener
      return app
    }),
    emitSecondInstance: () => secondInstanceListener?.(),
  }
  return app
}

function makeWindow(overrides: Partial<BrowserWindow> = {}): BrowserWindow {
  return {
    isDestroyed: jest.fn(() => false),
    isMinimized: jest.fn(() => false),
    restore: jest.fn(),
    focus: jest.fn(),
    ...overrides,
  } as unknown as BrowserWindow
}

it('calls requestSingleInstanceLock exactly once and returns true when acquired', () => {
  const app = makeApp(true)
  const result = enforceSingleInstance(app, () => null)
  expect(app.requestSingleInstanceLock).toHaveBeenCalledTimes(1)
  expect(result).toBe(true)
  expect(app.quit).not.toHaveBeenCalled()
})

it('quits and returns false when the lock is not acquired (second instance)', () => {
  const app = makeApp(false)
  const result = enforceSingleInstance(app, () => null)
  expect(result).toBe(false)
  expect(app.quit).toHaveBeenCalledTimes(1)
})

it('does NOT register a second-instance handler when the lock is not acquired', () => {
  const app = makeApp(false)
  enforceSingleInstance(app, () => null)
  expect(app.on).not.toHaveBeenCalled()
})

it('focuses the existing window on a second-instance event', () => {
  const app = makeApp(true)
  const window = makeWindow()
  enforceSingleInstance(app, () => window)

  app.emitSecondInstance()

  expect(window.focus).toHaveBeenCalledTimes(1)
})

it('restores a minimised window before focusing it', () => {
  const app = makeApp(true)
  const window = makeWindow({ isMinimized: jest.fn(() => true) as unknown as never })
  enforceSingleInstance(app, () => window)

  app.emitSecondInstance()

  expect(window.restore).toHaveBeenCalledTimes(1)
  expect(window.focus).toHaveBeenCalledTimes(1)
})

it('does not throw on a second-instance event when there is no window', () => {
  const app = makeApp(true)
  enforceSingleInstance(app, () => null)
  expect(() => app.emitSecondInstance()).not.toThrow()
})
