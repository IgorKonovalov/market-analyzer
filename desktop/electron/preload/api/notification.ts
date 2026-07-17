import { ipcRenderer } from 'electron'
import { IPC_CHANNELS } from '../../../shared/ipc-channels'
import type {
  NotificationShowPayload,
  NotificationShowResult,
} from '../../../shared/schemas/notification'

/**
 * OS-native notification bridge (Plan 0099 phase 4, ADR-0094). The renderer
 * always asks; MAIN decides whether to show (only when the window is
 * unfocused — the focused case has the in-app toast). Condition text only,
 * validated + length-capped at the main-process boundary.
 */
export const notificationApi = {
  show(payload: NotificationShowPayload): Promise<NotificationShowResult> {
    return ipcRenderer.invoke(IPC_CHANNELS.NOTIFICATION_SHOW, payload)
  },
}
