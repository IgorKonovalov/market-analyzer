/**
 * Preload: assembles `window.api` from per-domain modules and exposes it via
 * `contextBridge`. Per ADR-0008, the renderer never sees `require`, `process`,
 * or any Node global — only this typed surface.
 */
import { contextBridge } from "electron";
import { appApi } from "./api/app";
import { sidecarApi } from "./api/sidecar";
import { dialogApi } from "./api/dialog";
import { shellApi } from "./api/shell";

const api = {
  app: appApi,
  sidecar: sidecarApi,
  dialog: dialogApi,
  shell: shellApi,
} as const;

contextBridge.exposeInMainWorld("api", api);

export type ElectronAPI = typeof api;
