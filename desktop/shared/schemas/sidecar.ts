import { z } from 'zod'

export const SidecarPortSchema = z.object({
  port: z.number().int().positive(),
  secretToken: z.string().min(1),
})

export type SidecarPort = z.infer<typeof SidecarPortSchema>

/**
 * Status push events from the sidecar supervisor.
 *
 * Field contract (enforced via `superRefine`):
 *   - `secretToken` is required when `kind ∈ {'restarted', 'refreshed'}` (the
 *     renderer needs the bearer token) and forbidden otherwise — the secret
 *     never travels on `starting`, `ready`, `crashed`, or `fatal` payloads.
 *   - `port` is required when `kind === 'refreshed'` (the renderer's typed
 *     fetch client refreshes BOTH the port and the bearer; see Plan 0007
 *     phase 4.3 / ADR-0016 — a standalone-mode sidecar that restarted
 *     out-of-band can be on a different port) and forbidden otherwise. The
 *     deprecated `restarted` kind kept the port implicit; `refreshed` is the
 *     successor that fixes the gap.
 *   - `message` is required when `kind ∈ {'crashed', 'fatal'}` because those
 *     two events are user-facing (they reach the fatal-error window and
 *     in-app status surfaces); an empty crashed/fatal status would surface a
 *     blank dialog.
 */
export const SidecarStatusSchema = z
  .object({
    kind: z.enum(['starting', 'ready', 'crashed', 'restarted', 'fatal', 'refreshed']),
    message: z.string().optional(),
    pid: z.number().int().nullable().optional(),
    secretToken: z.string().min(1).optional(),
    port: z.number().int().positive().optional(),
  })
  .superRefine((val, ctx) => {
    const requiresSecret = val.kind === 'restarted' || val.kind === 'refreshed'
    if (requiresSecret && val.secretToken === undefined) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: `secretToken is required when kind is "${val.kind}"`,
        path: ['secretToken'],
      })
    } else if (!requiresSecret && val.secretToken !== undefined) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'secretToken must be absent when kind is not "restarted" or "refreshed"',
        path: ['secretToken'],
      })
    }
    if (val.kind === 'refreshed' && val.port === undefined) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'port is required when kind is "refreshed"',
        path: ['port'],
      })
    } else if (val.kind !== 'refreshed' && val.port !== undefined) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'port must be absent when kind is not "refreshed"',
        path: ['port'],
      })
    }
    if ((val.kind === 'crashed' || val.kind === 'fatal') && !val.message) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: `message is required when kind is "${val.kind}"`,
        path: ['message'],
      })
    }
  })

export type SidecarStatus = z.infer<typeof SidecarStatusSchema>
