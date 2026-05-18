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

export interface IpcDeps {
  supervisor: SidecarSupervisor
  info: SidecarInfo
}

export function registerIpcHandlers(deps: IpcDeps): void {
  registerAppHandlers(deps.supervisor)
  registerSidecarHandlers(deps.supervisor)
  registerDialogHandlers()
  registerShellHandlers()
}

export function cleanupServices(): void {
  cleanupAppHandlers()
  cleanupSidecarHandlers()
  cleanupDialogHandlers()
  cleanupShellHandlers()
}
