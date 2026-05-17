/**
 * Renderer-side fetch wrapper. Reads `{port, secretToken}` from the preload
 * bridge on first call and injects `Authorization: Bearer <secret>` plus the
 * sidecar base URL. The secret is held in a module-level closure — never
 * passed back across IPC, never written to disk, never logged.
 */
import type { SidecarPort } from "../../shared/schemas/sidecar";

let cached: SidecarPort | null = null;

async function getSidecarConfig(): Promise<SidecarPort> {
  if (cached) return cached;
  cached = await window.api.sidecar.getPort();
  return cached;
}

export async function sidecarFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const { port, secretToken } = await getSidecarConfig();
  const url = `http://127.0.0.1:${port}${path}`;
  const headers = new Headers(init.headers);
  if (path !== "/healthz") {
    headers.set("Authorization", `Bearer ${secretToken}`);
  }
  return fetch(url, { ...init, headers });
}
