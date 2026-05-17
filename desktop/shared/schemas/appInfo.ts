import { z } from "zod";

export const AppInfoSchema = z.object({
  version: z.string().min(1),
  sidecarOk: z.boolean(),
});

export type AppInfo = z.infer<typeof AppInfoSchema>;
