/**
 * `notification:show` payload boundary (Plan 0099 phase 4, ADR-0094):
 * condition text only, length-capped, strict keys — a compromised renderer
 * can neither spam wall-of-text toasts nor smuggle extra fields across.
 */
import {
  NOTIFICATION_BODY_MAX,
  NOTIFICATION_TITLE_MAX,
  NotificationShowSchema,
} from './notification'

it('accepts a condition-only title + body', () => {
  const parsed = NotificationShowSchema.parse({
    title: 'LP position out of range',
    body: 'LP out of range 6.2h — base pool 0xcdcd…cdcd',
  })
  expect(parsed.title).toContain('out of range')
})

it.each([
  ['missing title', { body: 'b' }],
  ['missing body', { title: 't' }],
  ['empty title', { title: '', body: 'b' }],
  ['over-length title', { title: 'x'.repeat(NOTIFICATION_TITLE_MAX + 1), body: 'b' }],
  ['over-length body', { title: 't', body: 'x'.repeat(NOTIFICATION_BODY_MAX + 1) }],
  ['smuggled extra key', { title: 't', body: 'b', action: 'rebalance' }],
])('rejects %s', (_label, raw) => {
  expect(NotificationShowSchema.safeParse(raw).success).toBe(false)
})
