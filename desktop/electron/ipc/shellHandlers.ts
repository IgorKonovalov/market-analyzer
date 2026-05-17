import { ipcMain, shell } from "electron";
import { IPC_CHANNELS } from "../../shared/ipc-channels";
import { ShellOpenExternalSchema } from "../../shared/schemas/shellOpen";

export function registerShellHandlers(): void {
  ipcMain.handle(IPC_CHANNELS.SHELL_OPEN_EXTERNAL, async (_event, raw: unknown) => {
    const { url } = ShellOpenExternalSchema.parse(raw);
    await shell.openExternal(url);
  });
}

export function cleanupShellHandlers(): void {
  ipcMain.removeHandler(IPC_CHANNELS.SHELL_OPEN_EXTERNAL);
}
