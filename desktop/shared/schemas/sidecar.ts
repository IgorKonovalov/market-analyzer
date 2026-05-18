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
 *   - `secretToken` is required when `kind === 'restarted'` (the renderer
 *     needs the new bearer token) and forbidden otherwise — the secret never
 *     travels on `starting`, `ready`, `crashed`, or `fatal` payloads.
 *   - `message` is required when `kind ∈ {'crashed', 'fatal'}` because those
 *     two events are user-facing (they reach the fatal-error window and
 *     in-app status surfaces); an empty crashed/fatal status would surface a
 *     blank dialog.
 */
export const SidecarStatusSchema = z
  .object({
    kind: z.enum(['starting', 'ready', 'crashed', 'restarted', 'fatal']),
    message: z.string().optional(),
    pid: z.number().int().nullable().optional(),
    secretToken: z.string().min(1).optional(),
  })
  .superRefine((val, ctx) => {
    if (val.kind === 'restarted' && val.secretToken === undefined) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'secretToken is required when kind is "restarted"',
        path: ['secretToken'],
      })
    } else if (val.kind !== 'restarted' && val.secretToken !== undefined) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'secretToken must be absent when kind is not "restarted"',
        path: ['secretToken'],
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
