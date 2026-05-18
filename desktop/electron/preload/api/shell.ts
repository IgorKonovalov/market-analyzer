import { ipcRenderer } from 'electron'
import { IPC_CHANNELS } from '../../../shared/ipc-channels'
import type { ShellOpenExternalPayload } from '../../../shared/schemas/shellOpen'

export const shellApi = {
  openExternal(payload: ShellOpenExternalPayload): Promise<void> {
    return ipcRenderer.invoke(IPC_CHANNELS.SHELL_OPEN_EXTERNAL, payload)
  },
}
