import { z } from 'zod'

/**
 * `notification:show` payload (Plan 0099 phase 4, ADR-0094). Condition text
 * only — a title and a body, both length-capped so a compromised renderer
 * cannot spam wall-of-text OS toasts. No URL, no action, no secret ever
 * crosses this channel; `.strict()` rejects anything smuggled alongside.
 */
export const NOTIFICATION_TITLE_MAX = 80
export const NOTIFICATION_BODY_MAX = 400

export const NotificationShowSchema = z
  .object({
    title: z.string().min(1).max(NOTIFICATION_TITLE_MAX),
    body: z.string().min(1).max(NOTIFICATION_BODY_MAX),
  })
  .strict()

export type NotificationShowPayload = z.infer<typeof NotificationShowSchema>

/** Result: whether main actually raised the OS toast (`false` when the window
 * was focused — the in-app toast covers that case — or when the platform does
 * not support notifications). */
export interface NotificationShowResult {
  shown: boolean
}
