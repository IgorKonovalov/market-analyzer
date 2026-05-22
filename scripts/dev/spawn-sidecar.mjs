#!/usr/bin/env node
/**
 * Wrap `uv run python -m market_analyser.api` so dev:all can manage the
 * sidecar's lifecycle uniformly.
 *
 * Phase 1 scope: spawn the Python sidecar, prefix its stdout/stderr lines with
 * `[sidecar]`, forward the child's exit code, and translate parent SIGINT /
 * SIGTERM into SIGTERM on the child. Argv past the wrapper passes through to
 * the sidecar verbatim (so future flags like --keep-sidecar — phase 3 — can be
 * intercepted before the passthrough, while unknown flags reach Python).
 *
 * Phase 3 extends this with --keep-sidecar (detach + don't kill on Ctrl+C),
 * tree-kill (kill the whole sidecar subtree, not just the immediate child),
 * and a reuse path for when a sidecar from a prior --keep-sidecar run is
 * still alive.
 */
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";

const PREFIX = "[sidecar]";
const PASSTHROUGH_ARGS = process.argv.slice(2);

const SIDECAR_ARGS = [
  "run",
  "python",
  "-m",
  "market_analyser.api",
  "--port=0",
  "--dev-origin=http://localhost:5173",
  ...PASSTHROUGH_ARGS,
];

const child = spawn("uv", SIDECAR_ARGS, {
  stdio: ["ignore", "pipe", "pipe"],
  env: process.env,
});

function prefixStream(stream, target) {
  const reader = createInterface({ input: stream });
  reader.on("line", (line) => {
    target.write(`${PREFIX} ${line}\n`);
  });
}

prefixStream(child.stdout, process.stdout);
prefixStream(child.stderr, process.stderr);

let shuttingDown = false;
function forwardSignal(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  if (child.exitCode !== null || child.killed) return;
  child.kill(signal);
}

process.on("SIGINT", () => forwardSignal("SIGTERM"));
process.on("SIGTERM", () => forwardSignal("SIGTERM"));

child.on("exit", (code, signal) => {
  if (signal && code === null) {
    process.exitCode = 128 + (signal === "SIGTERM" ? 15 : 2);
  } else {
    process.exitCode = code ?? 0;
  }
});

child.on("error", (err) => {
  process.stderr.write(`${PREFIX} failed to spawn uv: ${err.message}\n`);
  process.exitCode = 1;
});
