import { ApiError, sanitizeApiErrorBody } from './client'

describe('sanitizeApiErrorBody', () => {
  it('extracts FastAPI detail from a JSON body', () => {
    const out = sanitizeApiErrorBody('{"detail": "symbol not found"}')
    expect(out).toBe('symbol not found')
  })

  it('falls through to the raw body when JSON has no detail', () => {
    const out = sanitizeApiErrorBody('{"error": "something"}')
    expect(out).toBe('{"error": "something"}')
  })

  it('masks absolute Windows paths', () => {
    const out = sanitizeApiErrorBody('failed at C:\\Users\\alice\\AppData\\sidecar\\bar.py')
    expect(out).not.toContain('alice')
    expect(out).not.toContain('AppData')
    expect(out).toContain('<path>')
  })

  it('masks absolute POSIX paths', () => {
    const out = sanitizeApiErrorBody('failed at /Users/alice/code/sidecar/bar.py')
    expect(out).not.toContain('alice')
    expect(out).toContain('<path>')
  })

  it('drops Python traceback frames', () => {
    const body = [
      'Traceback (most recent call last):',
      '  File "/home/runner/app/main.py", line 42, in handler',
      '    raise ValueError("bad symbol")',
      'ValueError: bad symbol',
    ].join('\n')
    const out = sanitizeApiErrorBody(body)
    expect(out).not.toContain('Traceback')
    expect(out).not.toContain('main.py')
    expect(out).toContain('ValueError: bad symbol')
  })

  it('returns "(empty body)" for empty input', () => {
    expect(sanitizeApiErrorBody('')).toBe('(empty body)')
  })

  it('clamps absurdly long bodies', () => {
    const long = 'x'.repeat(2000)
    const out = sanitizeApiErrorBody(long)
    expect(out.length).toBeLessThanOrEqual(280)
    expect(out.endsWith('…')).toBe(true)
  })
})

describe('ApiError', () => {
  it('uses the sanitized body in .message and keeps the raw on .body', () => {
    const raw = '{"detail": "failed at C:\\\\Users\\\\alice\\\\code\\\\main.py line 12"}'
    const err = new ApiError(500, raw)
    expect(err.body).toBe(raw)
    expect(err.message).toContain('sidecar 500:')
    expect(err.message).not.toContain('alice')
    expect(err.message).toContain('<path>')
  })

  it('handles an empty body without losing the status', () => {
    const err = new ApiError(404, '')
    expect(err.message).toBe('sidecar 404: (empty body)')
    expect(err.body).toBe('')
  })
})

// ---------- Plan 0007 phase 4.4 — sidecar config refresh ---------- //
//
// These tests share the module-level `cached` and `configChangeSubscribers`
// state across cases. `client.ts`'s `ensureStatusListener` re-subscribes
// whenever `window.api.sidecar.onStatus`'s identity changes (the case in
// tests, where beforeEach installs a fresh capturing mock), so each test
// gets its own callable listener without needing `jest.isolateModules`.
import { sidecarFetch, subscribeToConfigChanges } from './client'

describe('client.ts — sidecar config refresh (phase 4.4)', () => {
  interface StatusEvent {
    kind: string
    port?: number
    secretToken?: string
    pid?: number | null
  }

  let capturedListener: ((status: StatusEvent) => void) | null = null
  let fetchSpy: jest.Mock

  beforeEach(() => {
    capturedListener = null
    ;(globalThis as unknown as { window: { api: unknown } }).window.api = {
      sidecar: {
        getPort: jest.fn().mockResolvedValue({ port: 50000, secretToken: 'initial-bearer' }),
        onStatus: jest.fn((cb: (status: StatusEvent) => void) => {
          capturedListener = cb
          return () => {
            capturedListener = null
          }
        }),
        refresh: jest.fn(),
      },
    }
    fetchSpy = jest.fn().mockResolvedValue({ ok: true, text: async () => '' })
    ;(globalThis as unknown as { fetch: unknown }).fetch = fetchSpy
  })

  function fireStatus(status: StatusEvent): void {
    if (!capturedListener) throw new Error('status listener was not registered yet')
    capturedListener(status)
  }

  it('on kind:"refreshed", subsequent sidecarFetch uses the new port and bearer (both fields)', async () => {
    // First sidecarFetch primes the cache via getPort AND triggers
    // ensureStatusListener (which registers our capturing onStatus).
    await sidecarFetch('/anything')
    expect(fetchSpy).toHaveBeenCalledTimes(1)
    const initialCallIdx = fetchSpy.mock.calls.length - 1
    const [initialUrl, initialInit] = fetchSpy.mock.calls[initialCallIdx] as [string, RequestInit]
    // Port might be whatever a prior test left in `cached`; assert via shape
    // and bearer instead of pinning to 50000.
    expect(initialUrl).toMatch(/^http:\/\/127\.0\.0\.1:\d+\/anything$/)
    expect((initialInit.headers as Headers).has('Authorization')).toBe(true)

    fireStatus({ kind: 'refreshed', port: 9999, secretToken: 'newbearer', pid: 4242 })

    await sidecarFetch('/anything')
    const [nextUrl, nextInit] = fetchSpy.mock.calls[fetchSpy.mock.calls.length - 1] as [
      string,
      RequestInit,
    ]
    expect(nextUrl).toBe('http://127.0.0.1:9999/anything')
    expect((nextInit.headers as Headers).get('Authorization')).toBe('Bearer newbearer')
  })

  it('subscribeToConfigChanges invokes the callback synchronously on cache update; unsubscribe stops further calls', async () => {
    // Prime the cache + register the capturing listener.
    await sidecarFetch('/seed')

    const cb = jest.fn()
    const unsubscribe = subscribeToConfigChanges(cb)

    fireStatus({ kind: 'refreshed', port: 9999, secretToken: 'newbearer' })
    // Synchronous — no awaits between fire and assert.
    expect(cb).toHaveBeenCalledTimes(1)

    fireStatus({ kind: 'refreshed', port: 8888, secretToken: 'evennewer' })
    expect(cb).toHaveBeenCalledTimes(2)

    unsubscribe()
    fireStatus({ kind: 'refreshed', port: 7777, secretToken: 'yetnewer' })
    expect(cb).toHaveBeenCalledTimes(2) // unsubscribed
  })

  it('legacy "restarted" branch still updates the bearer (no-op-tolerant fallback)', async () => {
    await sidecarFetch('/seed') // prime cache + register listener

    // Capture current port from the latest fetch call — it might not be 50000
    // if a prior test refreshed the cache.
    const [seedUrl] = fetchSpy.mock.calls[fetchSpy.mock.calls.length - 1] as [string]
    const portMatch = /:(\d+)\//.exec(seedUrl)
    if (!portMatch) throw new Error(`unexpected seed URL: ${seedUrl}`)
    const currentPort = portMatch[1]

    fireStatus({ kind: 'restarted', secretToken: 'legacy-rotated-bearer', pid: 999 })

    await sidecarFetch('/whatever')
    const [url, init] = fetchSpy.mock.calls[fetchSpy.mock.calls.length - 1] as [string, RequestInit]
    // Port unchanged (legacy 'restarted' carries no port — only bearer moves).
    expect(url).toBe(`http://127.0.0.1:${currentPort}/whatever`)
    expect((init.headers as Headers).get('Authorization')).toBe('Bearer legacy-rotated-bearer')
  })
})
