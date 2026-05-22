/**
 * Tests for scripts/dev/write-mcp-config.mjs — Plan 0015 phase 2.
 *
 * Asserts the four properties the plan's done-when names: the output parses
 * as JSON, the `mcpServers["market-analyser"]` entry has the expected shape,
 * the write is atomic (source uses `renameSync` + a `.tmp` staging path),
 * and POSIX file mode is 0600 (skipped on Windows where NTFS ACLs are
 * inherited from the user profile).
 *
 * Run with `node --test scripts/dev/__tests__/` or `pnpm test:dev-scripts`.
 */
import { describe, it, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const WRITER = path.resolve(__dirname, "..", "write-mcp-config.mjs");

const FIXTURE_LOCK = {
  pid: 12345,
  port: 53221,
  renderer_secret:
    "rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr",
  started_at: "2026-05-22T00:00:00Z",
  process_create_time: 1747749781.5,
  sidecar_version: "0.1.0",
};
const FIXTURE_SECRET = {
  secret: "ssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssss",
  created_at: "2026-05-22T00:00:00+00:00",
};

describe("write-mcp-config --once", () => {
  let tmpDir;
  let lockPath;
  let secretPath;
  let outPath;

  before(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "mcp-config-test-"));
    lockPath = path.join(tmpDir, "sidecar.lock");
    secretPath = path.join(tmpDir, "mcp-secret.json");
    outPath = path.join(tmpDir, ".mcp.json");
    fs.writeFileSync(lockPath, JSON.stringify(FIXTURE_LOCK));
    fs.writeFileSync(secretPath, JSON.stringify(FIXTURE_SECRET));

    const result = spawnSync(
      "node",
      [
        WRITER,
        "--once",
        `--lockfile=${lockPath}`,
        `--mcp-secret=${secretPath}`,
        `--out=${outPath}`,
      ],
      { encoding: "utf-8" },
    );
    if (result.status !== 0) {
      throw new Error(
        `writer exited ${result.status}\nstdout=${result.stdout}\nstderr=${result.stderr}`,
      );
    }
  });

  after(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("writes a file that parses as JSON", () => {
    const content = fs.readFileSync(outPath, "utf-8");
    JSON.parse(content);
  });

  it("contains mcpServers['market-analyser'] with the expected shape", () => {
    const config = JSON.parse(fs.readFileSync(outPath, "utf-8"));
    const server = config.mcpServers["market-analyser"];
    assert.equal(server.type, "http");
    assert.equal(server.url, `http://127.0.0.1:${FIXTURE_LOCK.port}/mcp`);
    assert.equal(server.headers.Authorization, `Bearer ${FIXTURE_SECRET.secret}`);
  });

  it("uses atomic write (.tmp staging + renameSync)", () => {
    const writerSrc = fs.readFileSync(WRITER, "utf-8");
    assert.match(writerSrc, /renameSync/, "writer should use fs.renameSync");
    assert.match(writerSrc, /\.tmp/, "writer should stage at <out>.tmp before rename");
  });

  it("sets POSIX mode 0600 (skipped on Windows)", (t) => {
    if (process.platform === "win32") {
      t.skip("POSIX file mode bits don't apply on Windows");
      return;
    }
    const stat = fs.statSync(outPath);
    assert.equal(stat.mode & 0o777, 0o600);
  });
});
