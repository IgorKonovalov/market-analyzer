/**
 * IPC channel names. Per ADR-0008 IPC discipline: NEVER use bare strings in
 * handlers or preload bindings. Adding a channel here is the discoverability
 * point; the architect review will flag drive-by additions.
 *
 * Renderer↔main is small by design. Domain operations (OHLCV, strategies,
 * backtests) go through the Python sidecar over HTTP, not through Electron IPC.
 */
export const IPC_CHANNELS = {
  APP_GET_INFO: 'app:get-info',
  SIDECAR_GET_PORT: 'sidecar:get-port',
  SIDECAR_STATUS: 'sidecar:status',
  // Plan 0007 phase 4.3: renderer-initiated re-attach. Returns the current
  // SidecarPort and emits a `sidecar:status` with `kind: 'refreshed'` as a
  // side-effect so other renderer subscribers can react too.
  SIDECAR_REFRESH: 'sidecar:refresh',
  DIALOG_OPEN_DIRECTORY: 'dialog:open-directory',
  SHELL_OPEN_EXTERNAL: 'shell:open-external',
} as const

export type IpcChannel = (typeof IPC_CHANNELS)[keyof typeof IPC_CHANNELS]
