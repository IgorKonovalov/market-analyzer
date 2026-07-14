#!/usr/bin/env node
/**
 * Wrap `uv run python -m market_analyser.api` so dev:all can manage the
 * sidecar's lifecycle uniformly.
 *
 * Three execution modes (see docs/onboarding/claude-code-setup.md):
 *
 *   default            spawn a fresh sidecar; SIGINT kills the whole subtree
 *                      (POSIX: kill the process group via `process.kill(-pid)`;
 *                       Windows: `taskkill /T /F`)
 *   --keep-sidecar     spawn a fresh sidecar detached; SIGINT leaves the
 *                      sidecar running so it outlives the dev:all session
 *                      (ADR-0016's standard outlive-the-viewer behaviour)
 *   reuse-existing     when sidecar.lock points at an already-alive owner,
 *                      do not spawn at all — log a reuse line and join the
 *                      rest of the dev:all chain. SIGINT in this mode never
 *                      attempts to kill anything (kill-only-what-you-spawned)
 *
 * Argv:
 *   --keep-sidecar           Recognised at any position. `--keep-sidecar=true`
 *                            is rejected (boolean flag, no value).
 *   --lockfile=<path>        Override sidecar.lock path. Default
 *                            <data-dir>/sidecar.lock.
 *   Everything else          Passes through to `python -m market_analyser.api`
 *                            verbatim.
 *
 * tree-kill is inlined (taskkill /T on Windows; kill the process group on
 * POSIX) so we don't accrue a root-level devDep just to issue two platform
 * commands. The current sidecar spawns no subprocesses, but using the
 * subtree-kill primitive instead of `child.kill` guards against future drift.
 */
import { spawn as nodeSpawn, spawnSync as nodeSpawnSync } from "node:child_process";
import { existsSync, rmSync } from "node:fs";
import { createInterface } from "node:readline";
import * as path from "node:path";
import { pathToFileURL } from "node:url";

import { resolveDataDir } from "./_lib/resolve-data-dir.mjs";

export const PREFIX = "[sidecar]";

const STATUS_ONELINER = [
  "import sys",
  "from pathlib import Path",
  "from market_analyser.api.lockfile import read_lockfile, is_owner_alive",
  "r = read_lockfile(Path(sys.argv[1]))",
  "print('absent' if r is None else "
  + "(f'alive {r.pid} {r.port}' if is_owner_alive(r) else 'stale'))",
].join("; ");

const SIDECAR_BASE_ARGS = [
  "run",
  "python",
  "-m",
  "market_analyser.api",
  "--port=0",
  "--dev-origin=http://localhost:5173",
];

export function parseArgs(argv) {
  const opts = { keepSidecar: false, lockfile: null, passthrough: [] };
  for (const arg of argv) {
    if (arg === "--keep-sidecar") {
      opts.keepSidecar = true;
    } else if (arg.startsWith("--keep-sidecar=")) {
      throw new Error(`${PREFIX} --keep-sidecar takes no value (got ${arg})`);
    } else if (arg.startsWith("--lockfile=")) {
      opts.lockfile = arg.slice("--lockfile=".length);
    } else {
      opts.passthrough.push(arg);
    }
  }
  return opts;
}

function defaultPythonStatusRunner(lockfilePath) {
  const result = nodeSpawnSync(
    "uv",
    ["run", "python", "-c", STATUS_ONELINER, lockfilePath],
    { encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"] },
  );
  if (result.error) {
    throw new Error(
      `${PREFIX} failed to invoke \`uv run python\` to probe sidecar.lock: ${result.error.message}`,
    );
  }
  if (result.status !== 0) {
    throw new Error(
      `${PREFIX} sidecar.lock probe exited ${result.status}:\n${result.stderr}`,
    );
  }
  return result.stdout;
}

export function checkSidecarStatus(lockfilePath, runner = defaultPythonStatusRunner) {
  const raw = runner(lockfilePath).trim();
  if (raw === "absent") return { status: "absent" };
  if (raw === "stale") return { status: "stale" };
  const m = raw.match(/^alive (\d+) (\d+)$/);
  if (m) return { status: "alive", pid: Number(m[1]), port: Number(m[2]) };
  throw new Error(`${PREFIX} unrecognised status output: ${JSON.stringify(raw)}`);
}

/**
 * Remove a stale (or malformed) sidecar.lock before dev-all launches its
 * children. The desktop chain is gated on `wait-on file:<lockfile>`, which
 * checks existence, not liveness — a leftover file from a force-killed prior
 * session opens that gate immediately, so Electron boots alongside the fresh
 * sidecar, reads the dead record, and cold-spawns a duplicate that then loses
 * the single-instance race ("sidecar already running at PID <N>"). Removing
 * the stale file up front makes wait-on gate on the NEW sidecar's lockfile
 * write, so Electron always takes the attach path under dev:all.
 *
 * A live owner is left untouched — spawn-sidecar's reuse path handles it. A
 * present-but-malformed file (status "absent") is removed too: it would open
 * the wait-on gate just the same.
 */
export function precleanStaleLockfile({
  lockfilePath,
  existsFn = existsSync,
  statusFn = checkSidecarStatus,
  rmFn = (p) => rmSync(p, { force: true }),
  log = (line) => process.stdout.write(`${line}\n`),
}) {
  if (!existsFn(lockfilePath)) return { removed: false, status: "absent" };
  const { status } = statusFn(lockfilePath);
  if (status === "alive") return { removed: false, status };
  rmFn(lockfilePath);
  log(
    `${PREFIX} removed ${status === "stale" ? "stale" : "malformed"} lockfile at `
    + `${lockfilePath} so the desktop chain waits for the fresh sidecar`,
  );
  return { removed: true, status };
}

export function killSubtree(pid, spawnSyncFn = nodeSpawnSync) {
  if (process.platform === "win32") {
    spawnSyncFn("taskkill", ["/pid", String(pid), "/T", "/F"], { stdio: "ignore" });
    return;
  }
  try {
    process.kill(-pid, "SIGTERM");
  } catch (err) {
    if (err.code === "ESRCH") return;
    try {
      process.kill(pid, "SIGTERM");
    } catch (innerErr) {
      if (innerErr.code !== "ESRCH") throw innerErr;
    }
  }
}

export function buildSpawnOptions({ keepSidecar }) {
  return {
    stdio: ["ignore", "pipe", "pipe"],
    detached: process.platform !== "win32" && keepSidecar,
    env: process.env,
  };
}

function prefixStream(stream, target) {
  const reader = createInterface({ input: stream });
  reader.on("line", (line) => {
    target.write(`${PREFIX} ${line}\n`);
  });
}

export function makeShutdown({ reused, keepSidecar, child, killSubtreeFn, exitFn }) {
  let shuttingDown = false;
  return function shutdown() {
    if (shuttingDown) return;
    shuttingDown = true;
    if (reused) {
      exitFn(0);
      return;
    }
    if (keepSidecar) {
      if (child) child.unref();
      exitFn(0);
      return;
    }
    if (child && child.exitCode === null && !child.killed) {
      killSubtreeFn(child.pid);
    }
  };
}

function defaultResolveLockfilePath() {
  return path.join(resolveDataDir(), "sidecar.lock");
}

export function runWrapper({
  argv,
  spawnFn = nodeSpawn,
  spawnSyncFn = nodeSpawnSync,
  statusFn = checkSidecarStatus,
  killSubtreeFn = (pid) => killSubtree(pid, spawnSyncFn),
  resolveLockfilePath = defaultResolveLockfilePath,
  log = (line) => process.stdout.write(`${line}\n`),
  errLog = (line) => process.stderr.write(`${line}\n`),
  registerSignals = true,
  exitFn = (code) => {
    process.exitCode = code;
  },
}) {
  const opts = parseArgs(argv);
  const lockfilePath = opts.lockfile ?? resolveLockfilePath();
  const status = statusFn(lockfilePath);

  if (status.status === "alive") {
    log(`${PREFIX} reusing already-running sidecar at PID ${status.pid}, port ${status.port}`);
    const shutdown = makeShutdown({
      reused: true,
      keepSidecar: opts.keepSidecar,
      child: null,
      killSubtreeFn,
      exitFn,
    });
    if (registerSignals) {
      process.on("SIGINT", shutdown);
      process.on("SIGTERM", shutdown);
    }
    return { reused: true, child: null, shutdown };
  }

  const child = spawnFn(
    "uv",
    [...SIDECAR_BASE_ARGS, ...opts.passthrough],
    buildSpawnOptions({ keepSidecar: opts.keepSidecar }),
  );

  if (child.stdout) prefixStream(child.stdout, process.stdout);
  if (child.stderr) prefixStream(child.stderr, process.stderr);

  if (typeof child.on === "function") {
    child.on("exit", (code, signal) => {
      if (signal && code === null) {
        exitFn(128 + (signal === "SIGTERM" ? 15 : 2));
      } else {
        exitFn(code ?? 0);
      }
    });
    child.on("error", (err) => {
      errLog(`${PREFIX} failed to spawn uv: ${err.message}`);
      exitFn(1);
    });
  }

  const shutdown = makeShutdown({
    reused: false,
    keepSidecar: opts.keepSidecar,
    child,
    killSubtreeFn,
    exitFn,
  });

  if (registerSignals) {
    process.on("SIGINT", shutdown);
    process.on("SIGTERM", shutdown);
  }

  return { reused: false, child, shutdown };
}

const isMain = process.argv[1]
  && pathToFileURL(process.argv[1]).href === import.meta.url;

if (isMain) {
  try {
    runWrapper({ argv: process.argv.slice(2) });
  } catch (err) {
    process.stderr.write(`${PREFIX} ${err.message}\n`);
    process.exitCode = 1;
  }
}
