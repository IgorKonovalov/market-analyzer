/**
 * `notification:show` handler (Plan 0099 phase 4, ADR-0094): the one place an
 * OS-native notification is raised. The renderer asks; main decides:
 *
 * - window focused → NOT shown (`{shown: false}`) — the in-app toast already
 *   covers the focused case, and double-signalling the same event is noise;
 * - window minimized/unfocused/absent → one native `Notification`, whose
 *   click restores + focuses the window (the 03:00-alert-reaches-the-user
 *   case ADR-0094 exists for);
 * - platform without notification support → `{shown: false}`, never a throw.
 *
 * The payload is Zod-validated at this boundary (condition text only,
 * length-capped — see `shared/schemas/notification.ts`); no URL, action, or
 * secret crosses the channel. Notify-while-fully-quit stays out of scope
 * (ADR-0016's deferred tray/supervisor).
 */
import { BrowserWindow, ipcMain, Notification } from 'electron'
import { IPC_CHANNELS } from '../../shared/ipc-channels'
import { NotificationShowSchema } from '../../shared/schemas/notification'
import type { NotificationShowResult } from '../../shared/schemas/notification'

export type GetMainWindow = () => BrowserWindow | null

export function registerNotificationHandlers(getMainWindow: GetMainWindow): void {
  ipcMain.handle(
    IPC_CHANNELS.NOTIFICATION_SHOW,
    async (_event, raw: unknown): Promise<NotificationShowResult> => {
      const { title, body } = NotificationShowSchema.parse(raw)

      const window = getMainWindow()
      const liveWindow = window !== null && !window.isDestroyed() ? window : null
      if (liveWindow !== null && liveWindow.isFocused()) {
        return { shown: false }
      }
      if (!Notification.isSupported()) {
        return { shown: false }
      }

      const notification = new Notification({ title, body })
      notification.on('click', () => {
        const target = getMainWindow()
        if (target === null || target.isDestroyed()) return
        if (target.isMinimized()) target.restore()
        target.show()
        target.focus()
      })
      notification.show()
      return { shown: true }
    },
  )
}

export function cleanupNotificationHandlers(): void {
  ipcMain.removeHandler(IPC_CHANNELS.NOTIFICATION_SHOW)
}
