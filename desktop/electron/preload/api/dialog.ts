import { ipcRenderer } from "electron";
import { IPC_CHANNELS } from "../../../shared/ipc-channels";
import type { OpenDirectoryResult } from "../../../shared/schemas/dialog";

export const dialogApi = {
  openDirectory(): Promise<OpenDirectoryResult> {
    return ipcRenderer.invoke(IPC_CHANNELS.DIALOG_OPEN_DIRECTORY);
  },
};
