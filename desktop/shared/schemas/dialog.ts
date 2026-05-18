import { z } from 'zod'

export const OpenDirectoryResultSchema = z.object({
  canceled: z.boolean(),
  path: z.string().nullable(),
})

export type OpenDirectoryResult = z.infer<typeof OpenDirectoryResultSchema>
