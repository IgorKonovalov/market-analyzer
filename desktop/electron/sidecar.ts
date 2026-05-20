/**
 * Python sidecar supervisor — standalone-mode (ADR-0016, Plan 0007 phase 1).
 *
 * Lifecycle is now decoupled from Electron:
 *   - On boot: read `<userData>/sidecar.lock` and run a PID-liveness probe. If
 *     the sidecar is already running, ATTACH (no spawn) — port + bearer come
 *     from the lockfile. If the lockfile is absent or stale, SPAWN
 *     `python -m market_analyser.api --port=0`. Either way, the lockfile is
 *     the source of truth for port + renderer_secret.
 *   - On Electron quit: the sidecar is NOT signalled. It outlives the viewer
 *     so an MCP client (e.g. Claude Code) can keep talking to it.
 *   - Stop is explicit: `python -m market_analyser.api stop` from a terminal,
 *     or the renderer's `POST /settings/stop` button.
 *
 * The supervisor still emits `starting` and `ready` status so the renderer's
 * existing readiness hook works. The `crashed` / `restarted` / `fatal` kinds
 * are no longer emitted (no crash supervision in standalone mode); the schema
 * still accepts them so the dead branches are inert.
 */
import { ChildProcess, spawn } from 'node:child_process'
import { promises as fs, existsSync } from 'node:fs'
import { resolve as resolvePath } from 'node:path'
import type { SidecarStatus } from '../shared/schemas/sidecar'

const LOCKFILE_NAME = 'sidecar.lock'
const LOCKFILE_POLL_MS = 100
const LOCKFILE_TIMEOUT_MS = 15_000
const HEALTHZ_TIMEOUT_MS = 10_000
const HEALTHZ_POLL_MS = 200

export interface SidecarInfo {
  port: number
  secretToken: string
  pid: number | null
}

interface LockfileRecord {
  pid: number
  port: number
  renderer_secret: string
  started_at: string
  process_create_time: number
  sidecar_version: string
}

type StatusListener = (status: SidecarStatus) => void

export interface AttachOrSpawnDeps {
  dataDir: string
  /** spawn(command, args, options) — injectable for unit tests. */
  spawnImpl?: (
    command: string,
    args: readonly string[],
    options: {
      cwd?: string
      env?: NodeJS.ProcessEnv
      stdio?: 'ignore' | 'pipe' | 'inherit' | (string | null | number)[]
    },
  ) => ChildProcess
  /** Test seam: returns true iff the PID is reachable. Defaults to `process.kill(pid, 0)`. */
  isPidAlive?: (pid: number) => boolean
  /** Test seam: read a file as text. Defaults to fs.promises.readFile. */
  readFileText?: (path: string) => Promise<string>
  /** Test seam: existence check. Defaults to existsSync. */
  fileExists?: (path: string) => boolean
  /** Test seam: GET /healthz. Defaults to global `fetch`. */
  healthz?: (url: string) => Promise<{ ok: boolean }>
  /** Resolved python executable. Defaults to the project's `.venv` python. */
  pythonExecutable?: string
  /** Optional dev-origin to inject into the spawned sidecar's CORS allowlist. */
  devOrigin?: string | null
}

export interface AttachOrSpawnResult {
  info: SidecarInfo
  spawnedChild: ChildProcess | null
  attached: boolean
}

/**
 * Read + parse the lockfile if present and PID-alive. Returns null if absent,
 * malformed, or the PID is gone (a stale lockfile is a spawn signal, not an
 * attach signal).
 */
async function readLiveLockfile(
  path: string,
  deps: Required<Pick<AttachOrSpawnDeps, 'isPidAlive' | 'readFileText' | 'fileExists'>>,
): Promise<LockfileRecord | null> {
  if (!deps.fileExists(path)) return null
  let text: string
  try {
    text = await deps.readFileText(path)
  } catch {
    return null
  }
  let record: LockfileRecord
  try {
    record = JSON.parse(text) as LockfileRecord
  } catch {
    return null
  }
  if (typeof record?.pid !== 'number' || typeof record?.port !== 'number') return null
  if (typeof record?.renderer_secret !== 'string') return null
  if (!deps.isPidAlive(record.pid)) return null
  return record
}

/**
 * Attach to an already-running sidecar, or spawn a fresh one.
 *
 * The PID-liveness probe is the JS-side gate: `process.kill(pid, 0)` (no
 * signal sent — just the existence check). The `process_create_time` cross
 * check from ADR-0016 runs on the Python side; the residual identity guard on
 * the JS side is the `/healthz` probe (next phase wires it).
 */
export async function attachOrSpawnSidecar(deps: AttachOrSpawnDeps): Promise<AttachOrSpawnResult> {
  const merged: Required<
    Pick<AttachOrSpawnDeps, 'isPidAlive' | 'readFileText' | 'fileExists' | 'spawnImpl' | 'healthz'>
  > = {
    isPidAlive: deps.isPidAlive ?? defaultIsPidAlive,
    readFileText: deps.readFileText ?? ((p) => fs.readFile(p, 'utf-8')),
    fileExists: deps.fileExists ?? existsSync,
    spawnImpl:
      deps.spawnImpl ??
      (spawn as AttachOrSpawnDeps['spawnImpl'] extends infer S ? NonNullable<S> : never),
    healthz: deps.healthz ?? defaultHealthz,
  }

  const lockfilePath = resolvePath(deps.dataDir, LOCKFILE_NAME)
  const existing = await readLiveLockfile(lockfilePath, merged)
  if (existing !== null) {
    return {
      info: {
        port: existing.port,
        secretToken: existing.renderer_secret,
        pid: existing.pid,
      },
      spawnedChild: null,
      attached: true,
    }
  }

  // Spawn path: no live sidecar. The sidecar picks its own port (`--port=0`),
  // generates its own renderer bearer, and writes both to the lockfile before
  // accepting requests. We poll the lockfile until it lands.
  const pythonExe = deps.pythonExecutable ?? resolveDefaultPython()
  const args = ['-m', 'market_analyser.api', '--port=0']
  if (deps.devOrigin) args.push(`--dev-origin=${deps.devOrigin}`)
  const env: NodeJS.ProcessEnv = { ...process.env, MARKET_ANALYSER_DATA_DIR: deps.dataDir }
  // ADR-0016: the sidecar generates its own renderer bearer on every boot;
  // any inherited MARKET_ANALYSER_SECRET would shadow the rotation property.
  delete env.MARKET_ANALYSER_SECRET

  const child = merged.spawnImpl(pythonExe, args, {
    cwd: resolvePath(__dirname, '..', '..', '..'),
    stdio: ['ignore', 'pipe', 'pipe'],
    env,
  })

  // Surface stderr so misconfiguration is visible in the dev console.
  child.stderr?.on('data', (chunk: Buffer) => {
    const line = chunk.toString().trim()
    if (line) console.error(`[sidecar] ${line}`)
  })

  // Poll until the lockfile reflects the live sidecar (well-formed JSON +
  // PID alive). A stale-but-still-present lockfile from the old PID is not
  // sufficient — we need the new sidecar's record. The Python side
  // atomically replaces via `os.replace`, so a single read here will see
  // either the old contents or the new.
  const spawned = await waitForLiveLockfile(lockfilePath, merged)
  await waitForHealthz(`http://127.0.0.1:${spawned.port}/healthz`, merged.healthz)

  return {
    info: {
      port: spawned.port,
      secretToken: spawned.renderer_secret,
      pid: spawned.pid,
    },
    spawnedChild: child,
    attached: false,
  }
}

async function waitForLiveLockfile(
  path: string,
  deps: Required<Pick<AttachOrSpawnDeps, 'isPidAlive' | 'readFileText' | 'fileExists'>>,
): Promise<LockfileRecord> {
  const deadline = Date.now() + LOCKFILE_TIMEOUT_MS
  while (Date.now() < deadline) {
    const record = await readLiveLockfile(path, deps)
    if (record !== null) return record
    await new Promise((r) => setTimeout(r, LOCKFILE_POLL_MS))
  }
  throw new Error(
    `sidecar did not write a live lockfile at ${path} within ${LOCKFILE_TIMEOUT_MS}ms`,
  )
}

async function waitForHealthz(
  url: string,
  healthz: (url: string) => Promise<{ ok: boolean }>,
): Promise<void> {
  const deadline = Date.now() + HEALTHZ_TIMEOUT_MS
  while (Date.now() < deadline) {
    try {
      const res = await healthz(url)
      if (res.ok) return
    } catch {
      // not yet listening
    }
    await new Promise((r) => setTimeout(r, HEALTHZ_POLL_MS))
  }
  throw new Error(`sidecar did not become healthy at ${url} within ${HEALTHZ_TIMEOUT_MS}ms`)
}

function defaultIsPidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

function defaultHealthz(url: string): Promise<{ ok: boolean }> {
  return fetch(url).then((r) => ({ ok: r.ok }))
}

function resolveDefaultPython(): string {
  if (process.env.MARKET_ANALYSER_PYTHON) return process.env.MARKET_ANALYSER_PYTHON
  const repoRoot = resolvePath(__dirname, '..', '..', '..')
  const venvPython =
    process.platform === 'win32'
      ? resolvePath(repoRoot, '.venv', 'Scripts', 'python.exe')
      : resolvePath(repoRoot, '.venv', 'bin', 'python')
  if (existsSync(venvPython)) return venvPython
  return 'python'
}

function computeDevOrigin(): string | null {
  const raw = process.env.ELECTRON_RENDERER_URL
  if (!raw) return null
  try {
    return new URL(raw).origin
  } catch {
    return null
  }
}

/**
 * Standalone-mode supervisor. Wraps `attachOrSpawnSidecar` and exposes the
 * status-stream + getInfo() surface that the rest of the app already wires
 * to. There is intentionally no `stop()` — the renderer's "Stop sidecar"
 * button calls the sidecar's `POST /settings/stop` endpoint, which the
 * sidecar handles itself.
 */
export class SidecarSupervisor {
  private spawnedChild: ChildProcess | null = null
  private readonly listeners = new Set<StatusListener>()
  private info: SidecarInfo | null = null
  readonly dataDir: string

  constructor(dataDir: string) {
    this.dataDir = dataDir
  }

  async start(): Promise<SidecarInfo> {
    this.emit({ kind: 'starting' })
    const result = await attachOrSpawnSidecar({
      dataDir: this.dataDir,
      devOrigin: computeDevOrigin(),
    })
    this.spawnedChild = result.spawnedChild
    this.info = result.info
    this.emit({ kind: 'ready', pid: result.info.pid })
    return result.info
  }

  getInfo(): SidecarInfo | null {
    return this.info
  }

  onStatus(listener: StatusListener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  /** Disengage from the spawned child without signalling it.
   * Called from `before-quit` so Electron exits cleanly while the sidecar
   * continues running (ADR-0016). Idempotent.
   */
  detach(): void {
    if (this.spawnedChild !== null) {
      this.spawnedChild.unref()
      this.spawnedChild = null
    }
  }

  private emit(status: SidecarStatus): void {
    for (const listener of this.listeners) listener(status)
  }
}
