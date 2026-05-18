import { ipcMain, dialog, BrowserWindow } from 'electron'
import { IPC_CHANNELS } from '../../shared/ipc-channels'
import { OpenDirectoryResultSchema, type OpenDirectoryResult } from '../../shared/schemas/dialog'

export function registerDialogHandlers(): void {
  ipcMain.handle(IPC_CHANNELS.DIALOG_OPEN_DIRECTORY, async (): Promise<OpenDirectoryResult> => {
    const window = BrowserWindow.getFocusedWindow()
    const result = window
      ? await dialog.showOpenDialog(window, { properties: ['openDirectory'] })
      : await dialog.showOpenDialog({ properties: ['openDirectory'] })
    return OpenDirectoryResultSchema.parse({
      canceled: result.canceled,
      path: result.filePaths[0] ?? null,
    })
  })
}

export function cleanupDialogHandlers(): void {
  ipcMain.removeHandler(IPC_CHANNELS.DIALOG_OPEN_DIRECTORY)
}
