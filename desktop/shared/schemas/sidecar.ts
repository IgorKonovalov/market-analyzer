import { z } from 'zod'

export const SidecarPortSchema = z.object({
  port: z.number().int().positive(),
  secretToken: z.string().min(1),
})

export type SidecarPort = z.infer<typeof SidecarPortSchema>

/**
 * Status push events from the sidecar supervisor.
 *
 * `secretToken` is required when `kind === 'restarted'` (the renderer needs the
 * new bearer token to keep talking to the restarted sidecar) and forbidden
 * otherwise — the secret never travels on `starting`, `ready`, `crashed`, or
 * `fatal` payloads. Encoded via `superRefine` to keep the inferred type simple.
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
  })

export type SidecarStatus = z.infer<typeof SidecarStatusSchema>
