import { ipcMain } from "electron";
import { IPC_CHANNELS } from "../../shared/ipc-channels";
import { SidecarPortSchema, type SidecarPort } from "../../shared/schemas/sidecar";
import type { SidecarSupervisor } from "../sidecar";

export function registerSidecarHandlers(supervisor: SidecarSupervisor): void {
  ipcMain.handle(IPC_CHANNELS.SIDECAR_GET_PORT, (): SidecarPort => {
    const info = supervisor.getInfo();
    if (info === null) {
      throw new Error("sidecar not yet ready");
    }
    return SidecarPortSchema.parse({ port: info.port, secretToken: info.secretToken });
  });
}

export function cleanupSidecarHandlers(): void {
  ipcMain.removeHandler(IPC_CHANNELS.SIDECAR_GET_PORT);
}
