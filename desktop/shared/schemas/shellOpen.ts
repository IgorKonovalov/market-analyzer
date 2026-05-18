import { z } from 'zod'

/**
 * External-URL opener payload. URLs must be http(s) — anything else is a path
 * injection (file://, javascript:) and rejected at the IPC boundary.
 */
export const ShellOpenExternalSchema = z.object({
  url: z
    .string()
    .url()
    .refine((u) => /^https?:\/\//i.test(u), {
      message: 'only http(s) URLs may be opened externally',
    }),
})

export type ShellOpenExternalPayload = z.infer<typeof ShellOpenExternalSchema>
