import { ipcMain, app } from "electron";
import { IPC_CHANNELS } from "../../shared/ipc-channels";
import { AppInfoSchema, type AppInfo } from "../../shared/schemas/appInfo";
import type { SidecarSupervisor } from "../sidecar";

export function registerAppHandlers(supervisor: SidecarSupervisor): void {
  ipcMain.handle(IPC_CHANNELS.APP_GET_INFO, (): AppInfo => {
    const payload = {
      version: app.getVersion(),
      sidecarOk: supervisor.getInfo() !== null,
    };
    return AppInfoSchema.parse(payload);
  });
}

export function cleanupAppHandlers(): void {
  ipcMain.removeHandler(IPC_CHANNELS.APP_GET_INFO);
}
