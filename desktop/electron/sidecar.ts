/**
 * Python sidecar supervisor.
 *
 * Picks a free port, generates a per-launch 32-byte hex bearer secret, spawns
 * `python -m market_analyser.api --port=<n>` with the secret injected via
 * `MARKET_ANALYSER_SECRET` in the child's environment (not argv — see Plan
 * 0004 phase 3 / ADR-0002 Notes), awaits a `PORT=<n>` line on stdout, then
 * polls `/healthz` until ready (max 10s).
 *
 * On crash (non-zero exit before shutdown): restart once with a freshly
 * rotated secret. A second crash surfaces a fatal-error window via the
 * status push channel.
 */
import { ChildProcess, spawn } from 'node:child_process'
import { createServer } from 'node:net'
import { randomBytes } from 'node:crypto'
import { existsSync } from 'node:fs'
import { resolve as resolvePath } from 'node:path'
import type { SidecarStatus } from '../shared/schemas/sidecar'

export interface SidecarInfo {
  port: number
  secretToken: string
  pid: number | null
}

type StatusListener = (status: SidecarStatus) => void

const HEALTHZ_TIMEOUT_MS = 10_000
const HEALTHZ_POLL_MS = 200
const SHUTDOWN_GRACE_MS = 3_000

export class SidecarSupervisor {
  private process: ChildProcess | null = null
  private restartedOnce = false
  private intentionalShutdown = false
  private readonly listeners = new Set<StatusListener>()
  private info: SidecarInfo | null = null

  async start(): Promise<SidecarInfo> {
    const port = await pickFreePort()
    const secretToken = randomBytes(32).toString('hex')
    await this.spawnSidecar(port, secretToken)
    this.info = { port, secretToken, pid: this.process?.pid ?? null }
    return this.info
  }

  getInfo(): SidecarInfo | null {
    return this.info
  }

  onStatus(listener: StatusListener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  async stop(): Promise<void> {
    this.intentionalShutdown = true
    if (!this.process || this.process.exitCode !== null) return
    const child = this.process
    await new Promise<void>((resolve) => {
      const killTimer = setTimeout(() => {
        if (child.exitCode === null) child.kill('SIGKILL')
      }, SHUTDOWN_GRACE_MS)
      child.once('exit', () => {
        clearTimeout(killTimer)
        resolve()
      })
      child.kill('SIGTERM')
    })
  }

  private emit(status: SidecarStatus): void {
    for (const listener of this.listeners) listener(status)
  }

  private async spawnSidecar(port: number, secretToken: string): Promise<void> {
    this.emit({ kind: 'starting' })
    const cwd = resolvePath(__dirname, '..', '..', '..')
    const pythonExecutable = resolvePythonExecutable(cwd)
    const child = spawn(pythonExecutable, ['-m', 'market_analyser.api', `--port=${port}`], {
      cwd,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, MARKET_ANALYSER_SECRET: secretToken },
    })
    this.process = child

    child.on('exit', (code) => this.handleExit(code))
    child.stderr?.on('data', (chunk: Buffer) => {
      const line = chunk.toString().trim()
      if (line) console.error(`[sidecar] ${line}`)
    })

    await waitForPortLine(child)
    await waitForHealthz(port, secretToken)
    this.emit({ kind: 'ready', pid: child.pid ?? null })
  }

  private async handleExit(code: number | null): Promise<void> {
    if (this.intentionalShutdown) return
    if (this.restartedOnce) {
      this.emit({
        kind: 'fatal',
        message: `sidecar crashed twice (exit=${code}); not restarting`,
      })
      return
    }
    this.restartedOnce = true
    this.emit({
      kind: 'crashed',
      message: `sidecar exited with code ${code}; restarting once`,
    })
    if (!this.info) {
      this.emit({ kind: 'fatal', message: 'no port to restart on' })
      return
    }
    try {
      const newSecret = randomBytes(32).toString('hex')
      await this.spawnSidecar(this.info.port, newSecret)
      this.info = { ...this.info, secretToken: newSecret, pid: this.process?.pid ?? null }
      this.emit({
        kind: 'restarted',
        pid: this.process?.pid ?? null,
        secretToken: newSecret,
      })
    } catch (err) {
      this.emit({
        kind: 'fatal',
        message: `restart failed: ${(err as Error).message}`,
      })
    }
  }
}

function resolvePythonExecutable(repoRoot: string): string {
  // Bare `python` on Windows often resolves to the WindowsApps shim, which has no
  // project deps. Prefer the uv venv interpreter; honour an explicit env override.
  if (process.env.MARKET_ANALYSER_PYTHON) return process.env.MARKET_ANALYSER_PYTHON
  const venvPython =
    process.platform === 'win32'
      ? resolvePath(repoRoot, '.venv', 'Scripts', 'python.exe')
      : resolvePath(repoRoot, '.venv', 'bin', 'python')
  if (existsSync(venvPython)) return venvPython
  return 'python'
}

function pickFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer()
    server.unref()
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const addr = server.address()
      if (addr && typeof addr === 'object') {
        const port = addr.port
        server.close(() => resolve(port))
      } else {
        reject(new Error('could not pick free port'))
      }
    })
  })
}

function waitForPortLine(child: ChildProcess): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error('sidecar did not print PORT line within 10s')),
      HEALTHZ_TIMEOUT_MS,
    )
    child.stdout?.on('data', (chunk: Buffer) => {
      const line = chunk.toString().trim()
      if (line.startsWith('PORT=')) {
        clearTimeout(timer)
        resolve()
      }
    })
    child.once('error', (err) => {
      clearTimeout(timer)
      reject(err)
    })
  })
}

async function waitForHealthz(port: number, _secret: string): Promise<void> {
  const url = `http://127.0.0.1:${port}/healthz`
  const deadline = Date.now() + HEALTHZ_TIMEOUT_MS
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url)
      if (response.ok) return
    } catch {
      // not yet listening
    }
    await new Promise((r) => setTimeout(r, HEALTHZ_POLL_MS))
  }
  throw new Error(`sidecar did not become healthy on port ${port} within 10s`)
}
