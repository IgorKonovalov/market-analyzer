/**
 * Composes per-domain IPC handler registrations into one `registerIpcHandlers`
 * call invoked from main.ts. `cleanupServices` is the mirror, invoked from the
 * `before-quit` hook so resources release in deterministic order.
 */
import type { SidecarSupervisor, SidecarInfo } from '../sidecar'
import { registerAppHandlers, cleanupAppHandlers } from './appHandlers'
import { registerSidecarHandlers, cleanupSidecarHandlers } from './sidecarHandlers'
import { registerDialogHandlers, cleanupDialogHandlers } from './dialogHandlers'
import { registerShellHandlers, cleanupShellHandlers } from './shellHandlers'
import {
  registerNotificationHandlers,
  cleanupNotificationHandlers,
  type GetMainWindow,
} from './notificationHandlers'

export interface IpcDeps {
  supervisor: SidecarSupervisor
  info: SidecarInfo
  /** Live main-window getter (Plan 0099 / ADR-0094) — the notification
   * handler reads focus state and focuses on click through this; a getter
   * (not a captured reference) because handlers register before the window
   * is created in `main.ts`. */
  getMainWindow: GetMainWindow
}

export function registerIpcHandlers(deps: IpcDeps): void {
  registerAppHandlers(deps.supervisor)
  registerSidecarHandlers(deps.supervisor)
  registerDialogHandlers()
  registerShellHandlers()
  registerNotificationHandlers(deps.getMainWindow)
}

export function cleanupServices(): void {
  cleanupAppHandlers()
  cleanupSidecarHandlers()
  cleanupDialogHandlers()
  cleanupShellHandlers()
  cleanupNotificationHandlers()
}
