import { ipcMain } from 'electron'
import { IPC_CHANNELS } from '../../shared/ipc-channels'
import { SidecarPortSchema, type SidecarPort } from '../../shared/schemas/sidecar'
import type { SidecarSupervisor } from '../sidecar'

export function registerSidecarHandlers(supervisor: SidecarSupervisor): void {
  ipcMain.handle(IPC_CHANNELS.SIDECAR_GET_PORT, (): SidecarPort => {
    const info = supervisor.getInfo()
    if (info === null) {
      throw new Error('sidecar not yet ready')
    }
    return SidecarPortSchema.parse({ port: info.port, secretToken: info.secretToken })
  })

  // Plan 0007 phase 4.3: renderer-initiated re-attach. The renderer calls
  // this when the EventSource has been failing repeatedly — the sidecar may
  // have been restarted out-of-band with a new port + bearer. The supervisor
  // coalesces concurrent calls, so a stampede of recovery attempts collapses
  // to a single attach-or-spawn cycle.
  ipcMain.handle(IPC_CHANNELS.SIDECAR_REFRESH, async (): Promise<SidecarPort> => {
    const info = await supervisor.refresh()
    return SidecarPortSchema.parse({ port: info.port, secretToken: info.secretToken })
  })
}

export function cleanupSidecarHandlers(): void {
  ipcMain.removeHandler(IPC_CHANNELS.SIDECAR_GET_PORT)
  ipcMain.removeHandler(IPC_CHANNELS.SIDECAR_REFRESH)
}
