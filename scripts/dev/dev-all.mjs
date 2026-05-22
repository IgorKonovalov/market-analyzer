#!/usr/bin/env node
/**
 * Orchestrate `pnpm dev:all` — Plan 0015's one-command sidecar + Electron loop.
 *
 * Resolves the canonical data dir once at boot (per ADR-0020), then runs two
 * children under `concurrently`:
 *
 *   1. spawn-sidecar.mjs — wraps the Python sidecar, owns its lifecycle.
 *   2. `wait-on file:<dir>/sidecar.lock` && `pnpm --filter desktop dev` —
 *      Electron stack gated on the lockfile so attach (ADR-0016) always wins
 *      over cold-spawn in dev mode.
 *
 * Phase 2 wires in a third child (write-mcp-config.mjs --watch). Phase 3
 * extends spawn-sidecar's signal handling for --keep-sidecar.
 *
 * Argv past `pnpm dev:all --` passes through to spawn-sidecar (e.g.
 * `pnpm dev:all -- --keep-sidecar`).
 */
import concurrently from "concurrently";

import { resolveDataDir } from "./_lib/resolve-data-dir.mjs";

const passthrough = process.argv.slice(2);
const dataDir = resolveDataDir();
const lockfilePath = `${dataDir}/sidecar.lock`;

const wrapperCmd = ["node", "scripts/dev/spawn-sidecar.mjs", ...passthrough].join(" ");
const desktopCmd =
  `wait-on "file:${lockfilePath}" --timeout 15000 && ` +
  "pnpm --filter @market-analyser/desktop dev";

const { result } = concurrently(
  [
    { name: "sidecar", command: wrapperCmd, prefixColor: "yellow" },
    { name: "desktop", command: desktopCmd, prefixColor: "cyan" },
  ],
  {
    prefix: "name",
    killOthers: ["failure", "success"],
    killSignal: "SIGTERM",
    restartTries: 0,
  },
);

try {
  await result;
} catch {
  process.exit(1);
}
