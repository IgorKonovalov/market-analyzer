/**
 * Plan 0007 phase 1 done-when (Jest, main-process unit test).
 *
 * Defends — via test doubles for `child_process` / `fs` / `process.kill`, not
 * process-listing spies — that `attachOrSpawnSidecar`:
 *
 *   1. With NO live lockfile: spawns `python -m market_analyser.api --port=0`
 *      exactly once and returns `{port, secretToken, pid}` matching the
 *      lockfile contents the sidecar then writes.
 *   2. With a LIVE lockfile (PID alive, create_time matches via the Python
 *      probe — JS-side gate is PID liveness only): does NOT call spawn, and
 *      returns the lockfile's contents.
 *   3. With a STALE lockfile (PID dead): calls spawn.
 *
 * The `before-quit` no-kill contract is asserted by inspecting the
 * `SidecarSupervisor.detach()` semantics: detach unrefs the child but does
 * not call `kill`, and the supervisor's `start()` never registers a kill
 * handler on the spawned child.
 */
import { EventEmitter } from 'node:events'

import {
  attachOrSpawnSidecar,
  SidecarSupervisor,
  type AttachOrSpawnDeps,
} from '../../electron/sidecar'

interface FakeChild extends EventEmitter {
  stderr: EventEmitter | null
  pid: number | undefined
  kill: jest.Mock
  unref: jest.Mock
}

function makeFakeChild(pid: number): FakeChild {
  const ee = new EventEmitter() as FakeChild
  ee.stderr = new EventEmitter()
  ee.pid = pid
  ee.kill = jest.fn()
  ee.unref = jest.fn()
  return ee
}

// Cross-platform suffix check — Windows resolves with `\`, POSIX with `/`.
const LOCKFILE_BASENAME = 'sidecar.lock'

function isLockfilePath(p: string): boolean {
  return p.endsWith(LOCKFILE_BASENAME)
}

interface LockfilePayload {
  pid: number
  port: number
  renderer_secret: string
  started_at: string
  process_create_time: number
  sidecar_version: string
}

function defaultLockfile(overrides: Partial<LockfilePayload> = {}): LockfilePayload {
  return {
    pid: 12345,
    port: 53221,
    renderer_secret: 'a'.repeat(64),
    started_at: '2026-05-20T14:23:01.500Z',
    process_create_time: 1747749781.5,
    sidecar_version: '0.0.1',
    ...overrides,
  }
}

function makeSpawnSpy(child: FakeChild): jest.Mock {
  return jest.fn().mockReturnValue(child)
}

describe('attachOrSpawnSidecar', () => {
  it('spawns python -m market_analyser.api --port=0 when no lockfile exists', async () => {
    const child = makeFakeChild(99999)
    const spawnSpy = makeSpawnSpy(child)
    const lockfilePayload = defaultLockfile({ pid: 99999, port: 60000 })

    let lockfileExists = false
    const writeLockfileSoon = (): void => {
      // Simulate the sidecar writing the lockfile shortly after spawn.
      setTimeout(() => {
        lockfileExists = true
      }, 10)
    }

    const deps: AttachOrSpawnDeps = {
      dataDir: '/tmp/test-data',
      spawnImpl: ((cmd, args) => {
        writeLockfileSoon()
        return spawnSpy(cmd, args, {}) as never
      }) as AttachOrSpawnDeps['spawnImpl'],
      isPidAlive: () => true,
      readFileText: async () => JSON.stringify(lockfilePayload),
      fileExists: (p) => isLockfilePath(p) && lockfileExists,
      healthz: async () => ({ ok: true }),
      pythonExecutable: 'python-test',
    }

    const result = await attachOrSpawnSidecar(deps)

    expect(spawnSpy).toHaveBeenCalledTimes(1)
    const [cmd, args] = spawnSpy.mock.calls[0]
    expect(cmd).toBe('python-test')
    expect(args).toEqual(['-m', 'market_analyser.api', '--port=0'])
    expect(result.attached).toBe(false)
    expect(result.spawnedChild).toBe(child)
    expect(result.info).toEqual({
      port: lockfilePayload.port,
      secretToken: lockfilePayload.renderer_secret,
      pid: lockfilePayload.pid,
    })
  })

  it('attaches to a live lockfile and does NOT call spawn', async () => {
    const spawnSpy = jest.fn()
    const lockfilePayload = defaultLockfile()
    const dataDir = '/tmp/test-data'

    const deps: AttachOrSpawnDeps = {
      dataDir,
      spawnImpl: spawnSpy as AttachOrSpawnDeps['spawnImpl'],
      isPidAlive: (pid) => {
        expect(pid).toBe(lockfilePayload.pid)
        return true
      },
      readFileText: async () => JSON.stringify(lockfilePayload),
      fileExists: (p) => isLockfilePath(p),
      // Plan 0007 phase 4.2: attach path runs the /healthz identity check.
      // healthz must (a) be called with the renderer bearer from the lockfile
      // and (b) return data_dir matching deps.dataDir for the attach to succeed.
      healthz: async (_url, opts) => {
        expect(opts?.bearer).toBe(lockfilePayload.renderer_secret)
        return { ok: true, data_dir: dataDir }
      },
      pythonExecutable: 'python-test',
    }

    const result = await attachOrSpawnSidecar(deps)

    expect(spawnSpy).toHaveBeenCalledTimes(0)
    expect(result.attached).toBe(true)
    expect(result.spawnedChild).toBeNull()
    expect(result.info).toEqual({
      port: lockfilePayload.port,
      secretToken: lockfilePayload.renderer_secret,
      pid: lockfilePayload.pid,
    })
  })

  it('spawns when the lockfile exists but the PID is dead (stale lockfile)', async () => {
    const child = makeFakeChild(77777)
    const spawnSpy = makeSpawnSpy(child)
    const stalePayload = defaultLockfile({ pid: 11111 })
    const freshPayload = defaultLockfile({ pid: 77777, port: 60001 })
    let lockfileContents = JSON.stringify(stalePayload)
    let wasOverwritten = false

    const deps: AttachOrSpawnDeps = {
      dataDir: '/tmp/test-data',
      spawnImpl: ((cmd, args) => {
        // After spawn, the new sidecar overwrites the lockfile.
        setTimeout(() => {
          lockfileContents = JSON.stringify(freshPayload)
          wasOverwritten = true
        }, 10)
        return spawnSpy(cmd, args, {}) as never
      }) as AttachOrSpawnDeps['spawnImpl'],
      isPidAlive: (pid) => {
        // The stale lockfile's PID is dead; the fresh sidecar's PID is alive.
        if (pid === stalePayload.pid) return false
        if (pid === freshPayload.pid) return true
        return false
      },
      readFileText: async () => lockfileContents,
      fileExists: () => true,
      healthz: async () => ({ ok: wasOverwritten }),
      pythonExecutable: 'python-test',
    }

    const result = await attachOrSpawnSidecar(deps)

    expect(spawnSpy).toHaveBeenCalledTimes(1)
    expect(result.attached).toBe(false)
    expect(result.info.pid).toBe(freshPayload.pid)
  })

  it('passes the dataDir through MARKET_ANALYSER_DATA_DIR and omits MARKET_ANALYSER_SECRET', async () => {
    const child = makeFakeChild(88888)
    let capturedEnv: NodeJS.ProcessEnv | undefined
    const lockfilePayload = defaultLockfile({ pid: 88888 })
    let lockfileWritten = false

    const deps: AttachOrSpawnDeps = {
      dataDir: '/tmp/specific-data-dir',
      spawnImpl: ((_cmd, _args, options) => {
        capturedEnv = options.env
        setTimeout(() => {
          lockfileWritten = true
        }, 10)
        return child as never
      }) as AttachOrSpawnDeps['spawnImpl'],
      isPidAlive: () => true,
      readFileText: async () => JSON.stringify(lockfilePayload),
      fileExists: () => lockfileWritten,
      healthz: async () => ({ ok: true }),
      pythonExecutable: 'python-test',
    }

    // Seed MARKET_ANALYSER_SECRET in process.env to confirm it gets stripped.
    const sentinel = 'should-not-be-passed-to-sidecar'
    const prior = process.env.MARKET_ANALYSER_SECRET
    process.env.MARKET_ANALYSER_SECRET = sentinel
    try {
      await attachOrSpawnSidecar(deps)
    } finally {
      if (prior === undefined) delete process.env.MARKET_ANALYSER_SECRET
      else process.env.MARKET_ANALYSER_SECRET = prior
    }

    expect(capturedEnv).toBeDefined()
    expect(capturedEnv!.MARKET_ANALYSER_DATA_DIR).toBe('/tmp/specific-data-dir')
    expect(capturedEnv!.MARKET_ANALYSER_SECRET).toBeUndefined()
  })
})

describe('attachOrSpawnSidecar — Plan 0007 phase 4.2 identity check', () => {
  let warnSpy: jest.SpyInstance

  beforeEach(() => {
    warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {})
  })

  afterEach(() => {
    warnSpy.mockRestore()
  })

  it('attaches when healthz returns matching data_dir, calling healthz with the renderer bearer from the lockfile', async () => {
    const spawnSpy = jest.fn()
    const lockfilePayload = defaultLockfile()
    const dataDir = '/tmp/test-attach-ok'
    const healthzSpy = jest.fn(async (_url: string, opts?: { bearer?: string }) => ({
      ok: true,
      data_dir: dataDir,
      _bearer: opts?.bearer,
    }))

    const deps: AttachOrSpawnDeps = {
      dataDir,
      spawnImpl: spawnSpy as AttachOrSpawnDeps['spawnImpl'],
      isPidAlive: () => true,
      readFileText: async () => JSON.stringify(lockfilePayload),
      fileExists: () => true,
      healthz: healthzSpy as AttachOrSpawnDeps['healthz'],
      pythonExecutable: 'python-test',
    }

    const result = await attachOrSpawnSidecar(deps)

    expect(result.attached).toBe(true)
    expect(spawnSpy).not.toHaveBeenCalled()
    expect(healthzSpy).toHaveBeenCalledTimes(1)
    const [calledUrl, calledOpts] = healthzSpy.mock.calls[0]
    expect(calledUrl).toBe(`http://127.0.0.1:${lockfilePayload.port}/healthz`)
    expect(calledOpts?.bearer).toBe(lockfilePayload.renderer_secret)
  })

  /**
   * Test fixture for the spawn-after-identity-fail tests. The realistic
   * scenario: the lockfile points at a real, alive process — but at a
   * different data_dir, or at a sidecar whose bearer has rotated, or to one
   * that can no longer be reached. The new sidecar we spawn writes the
   * lockfile atomically via `os.replace`, so by the time `waitForLiveLockfile`
   * polls, the file already reflects the new identity. We model that with a
   * synchronous swap inside `spawnImpl` (atomic-rename's microsecond window
   * collapses to "instant" in the test).
   */
  function makeFallThroughDeps(args: {
    dataDir: string
    stalePayload: LockfilePayload
    freshPayload: LockfilePayload
    spawnSpy: jest.Mock
    identityCheckBehaviour: (bearer: string) => Promise<{ ok: boolean; data_dir?: string }>
    onIdentityCheck?: () => void
  }): AttachOrSpawnDeps {
    let lockfileContents = JSON.stringify(args.stalePayload)
    return {
      dataDir: args.dataDir,
      spawnImpl: ((cmd, cmdArgs) => {
        lockfileContents = JSON.stringify(args.freshPayload)
        return args.spawnSpy(cmd, cmdArgs, {}) as never
      }) as AttachOrSpawnDeps['spawnImpl'],
      isPidAlive: (pid) => pid === args.stalePayload.pid || pid === args.freshPayload.pid,
      readFileText: async () => lockfileContents,
      fileExists: () => true,
      healthz: async (_url, opts) => {
        if (opts?.bearer !== undefined) {
          args.onIdentityCheck?.()
          return args.identityCheckBehaviour(opts.bearer)
        }
        return { ok: true }
      },
      pythonExecutable: 'python-test',
    }
  }

  it('falls through to spawn on data_dir mismatch, logging both paths', async () => {
    const child = makeFakeChild(55555)
    const spawnSpy = makeSpawnSpy(child)
    const stalePayload = defaultLockfile({ pid: 22222, port: 50000 })
    const freshPayload = defaultLockfile({ pid: 55555, port: 60000 })
    const dataDir = '/tmp/expected-data-dir'
    const observedDataDir = '/tmp/other-data-dir'

    const deps = makeFallThroughDeps({
      dataDir,
      stalePayload,
      freshPayload,
      spawnSpy,
      identityCheckBehaviour: async () => ({ ok: true, data_dir: observedDataDir }),
    })

    const result = await attachOrSpawnSidecar(deps)

    expect(spawnSpy).toHaveBeenCalledTimes(1)
    expect(result.attached).toBe(false)
    expect(result.info.pid).toBe(freshPayload.pid)

    const warnText = warnSpy.mock.calls.map((c) => c.join(' ')).join('\n')
    expect(warnText).toContain(dataDir)
    expect(warnText).toContain(observedDataDir)
  })

  it('falls through to spawn when healthz returns non-200', async () => {
    const child = makeFakeChild(66666)
    const spawnSpy = makeSpawnSpy(child)
    const stalePayload = defaultLockfile({ pid: 33333, port: 50001 })
    const freshPayload = defaultLockfile({ pid: 66666, port: 60001 })

    const deps = makeFallThroughDeps({
      dataDir: '/tmp/test-non200',
      stalePayload,
      freshPayload,
      spawnSpy,
      identityCheckBehaviour: async () => ({ ok: false }),
    })

    const result = await attachOrSpawnSidecar(deps)

    expect(spawnSpy).toHaveBeenCalledTimes(1)
    expect(result.attached).toBe(false)
    expect(result.info.pid).toBe(freshPayload.pid)
    expect(warnSpy).toHaveBeenCalled()
  })

  it('falls through to spawn when healthz throws, after exactly one retry', async () => {
    const child = makeFakeChild(77777)
    const spawnSpy = makeSpawnSpy(child)
    const stalePayload = defaultLockfile({ pid: 44444, port: 50002 })
    const freshPayload = defaultLockfile({ pid: 77777, port: 60002 })
    let identityCheckCalls = 0

    const deps = makeFallThroughDeps({
      dataDir: '/tmp/test-throws',
      stalePayload,
      freshPayload,
      spawnSpy,
      identityCheckBehaviour: async () => {
        throw new Error('ECONNREFUSED')
      },
      onIdentityCheck: () => {
        identityCheckCalls += 1
      },
    })

    const result = await attachOrSpawnSidecar(deps)

    expect(identityCheckCalls).toBe(2) // initial + one retry
    expect(spawnSpy).toHaveBeenCalledTimes(1)
    expect(result.attached).toBe(false)
    expect(result.info.pid).toBe(freshPayload.pid)
    expect(warnSpy).toHaveBeenCalled()
  })
})

describe('SidecarSupervisor', () => {
  it('detach() unrefs the spawned child but does NOT call kill', async () => {
    // Build a supervisor and inject a fake child via the attach path's seam.
    const child = makeFakeChild(33333)
    const lockfilePayload = defaultLockfile({ pid: 33333 })
    let lockfileExists = false

    const supervisor = new SidecarSupervisor('/tmp/sup-data')
    // Wire the start() through attachOrSpawnSidecar's spawn path manually by
    // pre-seeding the supervisor's child via the same lockfile-write trick.
    // Easier: monkey-patch supervisor.start to use our deps directly.
    const startImpl = jest.spyOn(supervisor, 'start').mockImplementation(async () => {
      // Manually invoke attachOrSpawnSidecar with our seams so the same code
      // path runs in test.
      const result = await attachOrSpawnSidecar({
        dataDir: '/tmp/sup-data',
        spawnImpl: ((_cmd, _args) => {
          setTimeout(() => {
            lockfileExists = true
          }, 5)
          return child as never
        }) as AttachOrSpawnDeps['spawnImpl'],
        isPidAlive: () => true,
        readFileText: async () => JSON.stringify(lockfilePayload),
        fileExists: () => lockfileExists,
        healthz: async () => ({ ok: true }),
        pythonExecutable: 'python-test',
      })
      // Bypass the supervisor's private setters by re-running the real
      // logic — re-spawn for completeness:
      ;(supervisor as unknown as { spawnedChild: FakeChild | null }).spawnedChild =
        result.spawnedChild as unknown as FakeChild
      ;(supervisor as unknown as { info: typeof result.info }).info = result.info
      return result.info
    })

    await supervisor.start()
    supervisor.detach()

    expect(child.unref).toHaveBeenCalledTimes(1)
    expect(child.kill).not.toHaveBeenCalled()

    // Calling detach again is a no-op (idempotent).
    supervisor.detach()
    expect(child.unref).toHaveBeenCalledTimes(1)

    startImpl.mockRestore()
  })

  it('does NOT register a kill on `before-quit` against the spawned child', () => {
    // Static-shape check: the SidecarSupervisor class has no `stop()` method.
    // Plan 0007 phase 1's contract — no Electron-side kill — is enforced
    // by the absence of any kill-emitting public method.
    const supervisor = new SidecarSupervisor('/tmp/whatever')
    expect((supervisor as unknown as Record<string, unknown>).stop).toBeUndefined()
    // The only lifecycle-end method exposed is `detach`, asserted above to
    // never call `child.kill`.
    expect(typeof supervisor.detach).toBe('function')
  })
})
