/**
 * Tests for scripts/dev/spawn-sidecar.mjs — Plan 0015 phase 3.
 *
 * Asserts the six behaviour properties the plan's done-when names:
 *   1. --keep-sidecar produces spawn options with `detached: true` on POSIX.
 *   2. Default-mode shutdown calls killSubtreeFn with the child's pid.
 *   3. --keep-sidecar shutdown calls child.unref() and does NOT killSubtreeFn.
 *   4. Reuse path (alive lockfile owner) does NOT call spawnFn.
 *   5. Reuse-mode shutdown attempts no kill (kill-only-what-you-spawned).
 *   6. Argv parsing: --keep-sidecar anywhere; --keep-sidecar=true rejected;
 *      unknown flags pass through.
 *
 * Tests inject mocks via runWrapper's dependency-injection points so signals
 * never reach the real process and the real Python venv is never touched.
 *
 * Run with `node --test scripts/dev/__tests__/spawn-sidecar.test.mjs` or
 * `pnpm test:dev-scripts`.
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  PREFIX,
  checkSidecarStatus,
  parseArgs,
  runWrapper,
} from "../spawn-sidecar.mjs";

function makeMockChild(pid = 9999) {
  const handlers = {};
  return {
    pid,
    stdout: null,
    stderr: null,
    exitCode: null,
    killed: false,
    on(event, cb) {
      handlers[event] = cb;
    },
    unref() {
      this._unrefCalled = true;
    },
    _handlers: handlers,
    _unrefCalled: false,
  };
}

function makeDeps({ statusResult = { status: "absent" } } = {}) {
  const spawnCalls = [];
  const killSubtreeCalls = [];
  const exitCalls = [];
  const logLines = [];
  const errLines = [];
  let mockChild = null;

  return {
    spawnFn: (...args) => {
      spawnCalls.push(args);
      mockChild = makeMockChild();
      return mockChild;
    },
    statusFn: () => statusResult,
    killSubtreeFn: (pid) => killSubtreeCalls.push(pid),
    resolveLockfilePath: () => "/fake/sidecar.lock",
    log: (m) => logLines.push(m),
    errLog: (m) => errLines.push(m),
    exitFn: (code) => exitCalls.push(code),
    registerSignals: false,
    capture: {
      spawnCalls,
      killSubtreeCalls,
      exitCalls,
      logLines,
      errLines,
      getChild: () => mockChild,
    },
  };
}

describe("parseArgs", () => {
  it("recognises --keep-sidecar at the first position", () => {
    const opts = parseArgs(["--keep-sidecar"]);
    assert.equal(opts.keepSidecar, true);
    assert.deepEqual(opts.passthrough, []);
  });

  it("recognises --keep-sidecar at a later position", () => {
    const opts = parseArgs(["--config=foo.json", "--keep-sidecar", "--debug"]);
    assert.equal(opts.keepSidecar, true);
    assert.deepEqual(opts.passthrough, ["--config=foo.json", "--debug"]);
  });

  it("rejects --keep-sidecar=true (boolean flag, no value)", () => {
    assert.throws(() => parseArgs(["--keep-sidecar=true"]), /takes no value/);
  });

  it("passes unknown flags through verbatim", () => {
    const opts = parseArgs(["--foo", "--bar=baz"]);
    assert.equal(opts.keepSidecar, false);
    assert.deepEqual(opts.passthrough, ["--foo", "--bar=baz"]);
  });

  it("extracts --lockfile=<path> override", () => {
    const opts = parseArgs(["--lockfile=/tmp/sidecar.lock"]);
    assert.equal(opts.lockfile, "/tmp/sidecar.lock");
    assert.deepEqual(opts.passthrough, []);
  });
});

describe("runWrapper — spawn path", () => {
  it("spawns the sidecar when no live owner is found", () => {
    const deps = makeDeps();
    runWrapper({ argv: [], ...deps });
    assert.equal(deps.capture.spawnCalls.length, 1);
    const [cmd, args] = deps.capture.spawnCalls[0];
    assert.equal(cmd, "uv");
    assert.ok(args.includes("market_analyser.api"));
  });

  it("passes unknown args through to the Python sidecar", () => {
    const deps = makeDeps();
    runWrapper({ argv: ["--config=foo.json"], ...deps });
    const [, args] = deps.capture.spawnCalls[0];
    assert.ok(args.includes("--config=foo.json"));
  });

  it("spawn options carry detached: true on POSIX when --keep-sidecar is set", (t) => {
    if (process.platform === "win32") {
      t.skip("detached: true only applies on POSIX; Windows uses taskkill /T");
      return;
    }
    const deps = makeDeps();
    runWrapper({ argv: ["--keep-sidecar"], ...deps });
    const [, , spawnOpts] = deps.capture.spawnCalls[0];
    assert.equal(spawnOpts.detached, true);
  });

  it("spawn options carry detached: false in default mode", () => {
    const deps = makeDeps();
    runWrapper({ argv: [], ...deps });
    const [, , spawnOpts] = deps.capture.spawnCalls[0];
    assert.equal(spawnOpts.detached, false);
  });

  it("default-mode shutdown invokes killSubtreeFn with the child's pid", () => {
    const deps = makeDeps();
    const { shutdown } = runWrapper({ argv: [], ...deps });
    shutdown();
    assert.deepEqual(deps.capture.killSubtreeCalls, [9999]);
    assert.equal(deps.capture.getChild()._unrefCalled, false);
  });

  it("default-mode shutdown is idempotent (called twice → killed once)", () => {
    const deps = makeDeps();
    const { shutdown } = runWrapper({ argv: [], ...deps });
    shutdown();
    shutdown();
    assert.equal(deps.capture.killSubtreeCalls.length, 1);
  });

  it("--keep-sidecar shutdown unrefs the child and does NOT killSubtreeFn", () => {
    const deps = makeDeps();
    const { shutdown } = runWrapper({ argv: ["--keep-sidecar"], ...deps });
    shutdown();
    assert.equal(deps.capture.getChild()._unrefCalled, true);
    assert.deepEqual(deps.capture.killSubtreeCalls, []);
    assert.deepEqual(deps.capture.exitCalls, [0]);
  });
});

describe("runWrapper — reuse path", () => {
  it("does NOT spawn when a live sidecar is detected", () => {
    const deps = makeDeps({
      statusResult: { status: "alive", pid: 4321, port: 53221 },
    });
    runWrapper({ argv: [], ...deps });
    assert.equal(deps.capture.spawnCalls.length, 0);
  });

  it("logs the reuse line with PID and port", () => {
    const deps = makeDeps({
      statusResult: { status: "alive", pid: 4321, port: 53221 },
    });
    runWrapper({ argv: [], ...deps });
    const reuseLine = deps.capture.logLines.find((line) =>
      line.includes("reusing already-running sidecar"),
    );
    assert.ok(
      reuseLine,
      `expected reuse log line, got: ${JSON.stringify(deps.capture.logLines)}`,
    );
    assert.match(reuseLine, /PID 4321/);
    assert.match(reuseLine, /port 53221/);
    assert.ok(reuseLine.startsWith(PREFIX));
  });

  it("reuse-mode shutdown attempts no kill, even in default mode", () => {
    const deps = makeDeps({
      statusResult: { status: "alive", pid: 4321, port: 53221 },
    });
    const { shutdown } = runWrapper({ argv: [], ...deps });
    shutdown();
    assert.deepEqual(deps.capture.killSubtreeCalls, []);
    assert.deepEqual(deps.capture.exitCalls, [0]);
  });

  it("reuse-mode shutdown attempts no kill, also in --keep-sidecar mode", () => {
    const deps = makeDeps({
      statusResult: { status: "alive", pid: 4321, port: 53221 },
    });
    const { shutdown } = runWrapper({ argv: ["--keep-sidecar"], ...deps });
    shutdown();
    assert.deepEqual(deps.capture.killSubtreeCalls, []);
    assert.deepEqual(deps.capture.exitCalls, [0]);
  });

  it("stale lockfile falls through to spawn (does NOT reuse)", () => {
    const deps = makeDeps({ statusResult: { status: "stale" } });
    runWrapper({ argv: [], ...deps });
    assert.equal(deps.capture.spawnCalls.length, 1);
  });
});

describe("checkSidecarStatus", () => {
  it("parses 'absent'", () => {
    assert.deepEqual(checkSidecarStatus("/x", () => "absent\n"), { status: "absent" });
  });

  it("parses 'stale'", () => {
    assert.deepEqual(checkSidecarStatus("/x", () => "stale\n"), { status: "stale" });
  });

  it("parses 'alive <pid> <port>'", () => {
    assert.deepEqual(
      checkSidecarStatus("/x", () => "alive 1234 5678\n"),
      { status: "alive", pid: 1234, port: 5678 },
    );
  });

  it("throws on unrecognised output", () => {
    assert.throws(
      () => checkSidecarStatus("/x", () => "unexpected\n"),
      /unrecognised status output/,
    );
  });
});
