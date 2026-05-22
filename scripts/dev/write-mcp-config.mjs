#!/usr/bin/env node
/**
 * Repo-root `.mcp.json` writer — Plan 0015 phase 2.
 *
 * Consumes two files the sidecar already produces:
 *   - `<data-dir>/sidecar.lock`     — ADR-0016. Carries `port`.
 *   - `<data-dir>/mcp-secret.json`  — ADR-0014. Carries `secret`.
 *
 * And produces:
 *   - `<repo-root>/.mcp.json`       — Claude Code's project-local MCP config.
 *
 * Writes are atomic: stage at `<out>.tmp`, then `fs.renameSync` to `<out>`.
 * POSIX file mode is reasserted at 0600 after rename so the bearer is not
 * world-readable. On Windows the OS inherits NTFS ACLs from the user profile
 * and we skip the chmod.
 *
 * CLI:
 *   --watch                  Run indefinitely; re-write on lockfile change.
 *   --once                   Read once and exit. Used by tests and ad-hoc runs.
 *   --lockfile=<path>        Override lockfile path. Default <data-dir>/sidecar.lock.
 *   --mcp-secret=<path>      Override mcp-secret path. Default <data-dir>/mcp-secret.json.
 *   --out=<path>             Override output path. Default <repo-root>/.mcp.json.
 *
 * The watcher polls the lockfile's `mtimeMs` on a short interval. `fs.watch` is
 * unreliable for rename-replace updates on Windows (the sidecar writes
 * `sidecar.lock.tmp` then `os.replace`s it), so we stick to a poll the plan
 * already permitted as the cross-platform fallback.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { resolveDataDir } from "./_lib/resolve-data-dir.mjs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "..", "..");

const PREFIX = "[mcp-config]";
const POLL_INTERVAL_MS = 500;

function parseArgs(argv) {
  const opts = { mode: "once", lockfile: null, mcpSecret: null, out: null };
  for (const arg of argv) {
    if (arg === "--watch") opts.mode = "watch";
    else if (arg === "--once") opts.mode = "once";
    else if (arg.startsWith("--lockfile=")) opts.lockfile = arg.slice("--lockfile=".length);
    else if (arg.startsWith("--mcp-secret=")) opts.mcpSecret = arg.slice("--mcp-secret=".length);
    else if (arg.startsWith("--out=")) opts.out = arg.slice("--out=".length);
    else throw new Error(`${PREFIX} unknown argument: ${arg}`);
  }
  return opts;
}

function resolvePaths(opts) {
  const needDataDir = !opts.lockfile || !opts.mcpSecret || !opts.out;
  const dataDir = needDataDir ? resolveDataDir() : null;
  return {
    lockfile: opts.lockfile ?? `${dataDir}/sidecar.lock`,
    mcpSecret: opts.mcpSecret ?? `${dataDir}/mcp-secret.json`,
    out: opts.out ?? path.join(REPO_ROOT, ".mcp.json"),
  };
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, { encoding: "utf-8" }));
}

function buildMcpConfig(port, secret) {
  return {
    mcpServers: {
      "market-analyser": {
        type: "http",
        url: `http://127.0.0.1:${port}/mcp`,
        headers: {
          Authorization: `Bearer ${secret}`,
        },
      },
    },
  };
}

function atomicWriteMcpConfig(outPath, config) {
  const dir = path.dirname(outPath);
  const tmpPath = path.join(dir, `${path.basename(outPath)}.tmp`);
  fs.writeFileSync(tmpPath, `${JSON.stringify(config, null, 2)}\n`, { encoding: "utf-8" });
  fs.renameSync(tmpPath, outPath);
  if (process.platform !== "win32") {
    fs.chmodSync(outPath, 0o600);
  }
}

function writeOnce(paths) {
  const lockRecord = readJson(paths.lockfile);
  const secretRecord = readJson(paths.mcpSecret);
  if (typeof lockRecord.port !== "number") {
    throw new Error(`${PREFIX} ${paths.lockfile}: missing or non-numeric \`port\` field`);
  }
  if (typeof secretRecord.secret !== "string" || !secretRecord.secret) {
    throw new Error(`${PREFIX} ${paths.mcpSecret}: missing or empty \`secret\` field`);
  }
  const config = buildMcpConfig(lockRecord.port, secretRecord.secret);
  atomicWriteMcpConfig(paths.out, config);
  process.stdout.write(`${PREFIX} wrote ${paths.out} (port=${lockRecord.port})\n`);
}

function startWatch(paths) {
  let lastMtimeMs = 0;
  let lastErrorMessage = null;
  function tick() {
    try {
      const stat = fs.statSync(paths.lockfile);
      if (stat.mtimeMs === lastMtimeMs) return;
      lastMtimeMs = stat.mtimeMs;
      writeOnce(paths);
      lastErrorMessage = null;
    } catch (err) {
      if (err.code === "ENOENT") {
        lastMtimeMs = 0;
        return;
      }
      if (lastErrorMessage !== err.message) {
        process.stderr.write(`${PREFIX} ${err.message}\n`);
        lastErrorMessage = err.message;
      }
    }
  }
  const timer = setInterval(tick, POLL_INTERVAL_MS);
  tick();
  function shutdown() {
    clearInterval(timer);
    process.exit(0);
  }
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
  process.stdout.write(`${PREFIX} watching ${paths.lockfile}\n`);
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  const paths = resolvePaths(opts);
  if (opts.mode === "once") {
    writeOnce(paths);
  } else {
    startWatch(paths);
  }
}

main();
