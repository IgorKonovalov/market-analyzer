/**
 * Plan 0007 phase 4.3 done-when: the `sidecar:refresh` IPC channel exists,
 * is registered exactly once, and returns the same shape as
 * `sidecar:get-port`. Also asserts the channel string itself so an accidental
 * rename in `ipc-channels.ts` fails this spec, not just a runtime call.
 *
 * The spec stubs `electron.ipcMain.handle` rather than running real Electron;
 * the test environment is `node`, the unit under test is the handler-
 * registration wiring under `desktop/electron/ipc/`.
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

jest.mock('electron', () => ({
  ipcMain: {
    handle: handleSpy,
    removeHandler: removeHandlerSpy,
  },
}))

import { registerSidecarHandlers, cleanupSidecarHandlers } from '../../electron/ipc/sidecarHandlers'
import { IPC_CHANNELS } from '../../shared/ipc-channels'
import type { SidecarSupervisor, SidecarInfo } from '../../electron/sidecar'

function fakeSupervisor(overrides: Partial<SidecarSupervisor> = {}): SidecarSupervisor {
  const info: SidecarInfo = { port: 60000, secretToken: 'a'.repeat(64), pid: 9999 }
  const refreshedInfo: SidecarInfo = { port: 60001, secretToken: 'b'.repeat(64), pid: 9998 }
  return {
    getInfo: jest.fn(() => info),
    refresh: jest.fn(async () => refreshedInfo),
    ...overrides,
  } as unknown as SidecarSupervisor
}

describe('registerSidecarHandlers', () => {
  beforeEach(() => {
    handlers.clear()
    handleSpy.mockClear()
    removeHandlerSpy.mockClear()
  })

  it('registers SIDECAR_REFRESH exactly once', () => {
    const supervisor = fakeSupervisor()
    registerSidecarHandlers(supervisor)
    const refreshRegistrations = handleSpy.mock.calls.filter(
      (c) => c[0] === IPC_CHANNELS.SIDECAR_REFRESH,
    )
    expect(refreshRegistrations.length).toBe(1)
    expect(IPC_CHANNELS.SIDECAR_REFRESH).toBe('sidecar:refresh')
  })

  it('SIDECAR_REFRESH handler returns the SidecarPort shape produced by supervisor.refresh()', async () => {
    const refreshedInfo: SidecarInfo = {
      port: 60001,
      secretToken: 'b'.repeat(64),
      pid: 9998,
    }
    const refreshSpy = jest.fn(async () => refreshedInfo)
    const supervisor = fakeSupervisor({
      refresh: refreshSpy,
    } as unknown as Partial<SidecarSupervisor>)
    registerSidecarHandlers(supervisor)

    const handler = handlers.get(IPC_CHANNELS.SIDECAR_REFRESH)
    expect(handler).toBeDefined()
    const result = (await handler!({})) as { port: number; secretToken: string }
    expect(result).toEqual({ port: refreshedInfo.port, secretToken: refreshedInfo.secretToken })
    expect(refreshSpy).toHaveBeenCalledTimes(1)
  })

  it('cleanup removes the SIDECAR_REFRESH handler', () => {
    const supervisor = fakeSupervisor()
    registerSidecarHandlers(supervisor)
    expect(handlers.has(IPC_CHANNELS.SIDECAR_REFRESH)).toBe(true)
    cleanupSidecarHandlers()
    expect(removeHandlerSpy).toHaveBeenCalledWith(IPC_CHANNELS.SIDECAR_REFRESH)
  })

  it('SIDECAR_GET_PORT continues to work after the refresh handler is added (regression)', async () => {
    const supervisor = fakeSupervisor()
    registerSidecarHandlers(supervisor)
    const handler = handlers.get(IPC_CHANNELS.SIDECAR_GET_PORT)
    expect(handler).toBeDefined()
    const result = (await handler!({})) as { port: number; secretToken: string }
    expect(result.port).toBe(60000)
  })
})
