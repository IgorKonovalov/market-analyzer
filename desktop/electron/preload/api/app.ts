import { ipcRenderer } from 'electron'
import { IPC_CHANNELS } from '../../../shared/ipc-channels'
import type { AppInfo } from '../../../shared/schemas/appInfo'

export const appApi = {
  getInfo(): Promise<AppInfo> {
    return ipcRenderer.invoke(IPC_CHANNELS.APP_GET_INFO)
  },
}
