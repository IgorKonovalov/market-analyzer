import { z } from "zod";

export const SidecarPortSchema = z.object({
  port: z.number().int().positive(),
  secretToken: z.string().min(1),
});

export type SidecarPort = z.infer<typeof SidecarPortSchema>;

export const SidecarStatusSchema = z.object({
  kind: z.enum(["starting", "ready", "crashed", "restarted", "fatal"]),
  message: z.string().optional(),
  pid: z.number().int().nullable().optional(),
});

export type SidecarStatus = z.infer<typeof SidecarStatusSchema>;
