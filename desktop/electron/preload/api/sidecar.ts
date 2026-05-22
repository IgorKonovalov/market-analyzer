import { ipcRenderer } from 'electron'
import { IPC_CHANNELS } from '../../../shared/ipc-channels'
import type { SidecarPort, SidecarStatus } from '../../../shared/schemas/sidecar'
import type { Unsubscribe } from '../../../shared/types/api'

export const sidecarApi = {
  getPort(): Promise<SidecarPort> {
    return ipcRenderer.invoke(IPC_CHANNELS.SIDECAR_GET_PORT)
  },

  onStatus(callback: (status: SidecarStatus) => void): Unsubscribe {
    const handler = (_event: Electron.IpcRendererEvent, status: SidecarStatus): void =>
      callback(status)
    ipcRenderer.on(IPC_CHANNELS.SIDECAR_STATUS, handler)
    return () => ipcRenderer.off(IPC_CHANNELS.SIDECAR_STATUS, handler)
  },

  // Plan 0007 phase 4.3: renderer-initiated re-attach. The
  // `useEventStream` hook calls this when its EventSource has been failing
  // repeatedly without an intervening open — the sidecar may have been
  // restarted out-of-band with a new port + bearer.
  refresh(): Promise<SidecarPort> {
    return ipcRenderer.invoke(IPC_CHANNELS.SIDECAR_REFRESH)
  },
}
