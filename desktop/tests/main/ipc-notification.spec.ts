/**
 * Plan 0099 phase 4 done-when, main-process half (ADR-0094):
 * (a) with the window unfocused, one `notification:show` call raises exactly
 *     one native Notification whose click restores + focuses the window;
 * (b) with the window focused, NO OS notification is raised (`shown: false`)
 *     — the in-app toast covers that case, no double-signal;
 * plus the boundary: payloads are Zod-validated (length caps, strict keys),
 * an unsupported platform degrades to `{shown: false}`, and the handler
 * registers/cleans up exactly once.
 *
 * The spec stubs `electron` (ipcMain + Notification) rather than running real
 * Electron; the unit under test is the handler wiring in
 * `desktop/electron/ipc/notificationHandlers.ts`.
 */

type IpcHandler = (event: unknown, ...args: unknown[]) => unknown
const handlers = new Map<string, IpcHandler>()
const handleSpy = jest.fn((channel: string, listener: IpcHandler) => {
  if (handlers.has(channel)) {
    throw new Error(`channel "${channel}" registered twice`)
  }
  handlers.set(channel, listener)
})
const removeHandlerSpy = jest.fn((channel: string) => {
  handlers.delete(channel)
})

interface NotificationInstance {
  options: { title: string; body: string }
  show: jest.Mock
  clickListeners: Array<() => void>
}
const notificationInstances: NotificationInstance[] = []
let notificationSupported = true

class FakeNotification {
  options: { title: string; body: string }
  show = jest.fn()
  clickListeners: Array<() => void> = []

  constructor(options: { title: string; body: string }) {
    this.options = options
    notificationInstances.push(this)
  }

  on(event: string, listener: () => void): void {
    if (event === 'click') this.clickListeners.push(listener)
  }

  static isSupported(): boolean {
    return notificationSupported
  }
}

jest.mock('electron', () => ({
  ipcMain: {
    handle: handleSpy,
    removeHandler: removeHandlerSpy,
  },
  Notification: FakeNotification,
}))

import {
  registerNotificationHandlers,
  cleanupNotificationHandlers,
} from '../../electron/ipc/notificationHandlers'
import { IPC_CHANNELS } from '../../shared/ipc-channels'
import type { BrowserWindow } from 'electron'

interface FakeWindow {
  isDestroyed: jest.Mock
  isFocused: jest.Mock
  isMinimized: jest.Mock
  restore: jest.Mock
  show: jest.Mock
  focus: jest.Mock
}

function fakeWindow(overrides: Partial<Record<keyof FakeWindow, boolean>> = {}): FakeWindow {
  return {
    isDestroyed: jest.fn(() => overrides.isDestroyed ?? false),
    isFocused: jest.fn(() => overrides.isFocused ?? false),
    isMinimized: jest.fn(() => overrides.isMinimized ?? false),
    restore: jest.fn(),
    show: jest.fn(),
    focus: jest.fn(),
  }
}

function asBrowserWindow(window: FakeWindow | null): BrowserWindow | null {
  return window as unknown as BrowserWindow | null
}

const PAYLOAD = { title: 'LP position out of range', body: 'LP out of range 6.2h — base pool' }

async function invoke(raw: unknown): Promise<{ shown: boolean }> {
  const handler = handlers.get(IPC_CHANNELS.NOTIFICATION_SHOW)
  expect(handler).toBeDefined()
  return (await handler!({}, raw)) as { shown: boolean }
}

describe('registerNotificationHandlers', () => {
  beforeEach(() => {
    handlers.clear()
    handleSpy.mockClear()
    removeHandlerSpy.mockClear()
    notificationInstances.length = 0
    notificationSupported = true
  })

  it('registers NOTIFICATION_SHOW exactly once, and cleanup removes it', () => {
    registerNotificationHandlers(() => null)
    const registrations = handleSpy.mock.calls.filter(
      (c) => c[0] === IPC_CHANNELS.NOTIFICATION_SHOW,
    )
    expect(registrations.length).toBe(1)
    expect(IPC_CHANNELS.NOTIFICATION_SHOW).toBe('notification:show')

    cleanupNotificationHandlers()
    expect(removeHandlerSpy).toHaveBeenCalledWith(IPC_CHANNELS.NOTIFICATION_SHOW)
  })

  it('unfocused window: raises exactly one Notification whose click restores + focuses', async () => {
    const window = fakeWindow({ isFocused: false, isMinimized: true })
    registerNotificationHandlers(() => asBrowserWindow(window))

    const result = await invoke(PAYLOAD)

    expect(result).toEqual({ shown: true })
    expect(notificationInstances).toHaveLength(1)
    const instance = notificationInstances[0]
    expect(instance.options).toEqual(PAYLOAD)
    expect(instance.show).toHaveBeenCalledTimes(1)

    // Click focuses/restores the app (done-when (a), click half).
    expect(instance.clickListeners).toHaveLength(1)
    instance.clickListeners[0]()
    expect(window.restore).toHaveBeenCalledTimes(1)
    expect(window.show).toHaveBeenCalledTimes(1)
    expect(window.focus).toHaveBeenCalledTimes(1)
  })

  it('focused window: raises NO OS notification (no double-signal)', async () => {
    const window = fakeWindow({ isFocused: true })
    registerNotificationHandlers(() => asBrowserWindow(window))

    const result = await invoke(PAYLOAD)

    expect(result).toEqual({ shown: false })
    expect(notificationInstances).toHaveLength(0)
  })

  it('absent or destroyed window still notifies (app running, window closed)', async () => {
    registerNotificationHandlers(() => null)
    expect(await invoke(PAYLOAD)).toEqual({ shown: true })
    expect(notificationInstances).toHaveLength(1)
  })

  it('unsupported platform degrades to shown:false, never a throw', async () => {
    notificationSupported = false
    registerNotificationHandlers(() => asBrowserWindow(fakeWindow()))
    expect(await invoke(PAYLOAD)).toEqual({ shown: false })
    expect(notificationInstances).toHaveLength(0)
  })

  it('rejects malformed payloads at the Zod boundary', async () => {
    registerNotificationHandlers(() => null)

    await expect(invoke({ title: 'no body' })).rejects.toThrow()
    await expect(invoke({ ...PAYLOAD, body: 'x'.repeat(500) })).rejects.toThrow()
    await expect(invoke({ ...PAYLOAD, url: 'https://evil.example' })).rejects.toThrow()
    expect(notificationInstances).toHaveLength(0)
  })
})
