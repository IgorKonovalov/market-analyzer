/**
 * Plan 0001 phase 4 done-when (rewritten under Plan 0004 phase 2):
 *   - Supervisor restarts the python sidecar once after a crash.
 *   - The new bearer secret reaches the sidecar via the IPC channel and a
 *     renderer-context fetch with the rotated secret succeeds (proves the
 *     sidecar accepts the rotated bearer; the renderer's in-memory cache
 *     invalidation is covered by the SidecarStatusSchema unit test).
 *   - A second crash surfaces the fatal-error window.
 *
 * Requires a built desktop bundle and `MARKET_ANALYSER_E2E=1` set by
 * `playwright-global-setup.mjs` so the main process exposes its supervisor on
 * globalThis. Run with `pnpm --filter desktop test:e2e`.
 */
import { _electron as electron, test, expect, type Page } from '@playwright/test'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))

interface SupervisorInfo {
  pid: number | null
  port: number
  secretToken: string
}

interface MinimalSupervisor {
  getInfo(): SupervisorInfo | null
}

async function readSupervisorInfo(app: {
  evaluate<T>(fn: () => T): Promise<T>
}): Promise<SupervisorInfo | null> {
  return app.evaluate(() => {
    const sup = (globalThis as { __sidecarSupervisor?: MinimalSupervisor }).__sidecarSupervisor
    return sup?.getInfo() ?? null
  })
}

async function pollForChangedPid(
  app: { evaluate<T>(fn: () => T): Promise<T> },
  previousPid: number,
  timeoutMs: number,
): Promise<SupervisorInfo | null> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const info = await readSupervisorInfo(app)
    if (info !== null && info.pid !== null && info.pid !== previousPid) return info
    await new Promise((r) => setTimeout(r, 500))
  }
  return null
}

async function findFatalWindow(windows: Page[]): Promise<Page | null> {
  for (const w of windows) {
    try {
      const url = w.url()
      // showFatalWindow loads a data:text/html URL whose body contains the
      // "market-analyser stopped" marker. The BrowserWindow `title:` option
      // sets the OS title but never reaches document.title — detection has to
      // look at body, not page.title().
      if (url.startsWith('data:text/html') && url.includes('market-analyser%20stopped')) {
        return w
      }
    } catch {
      // page may be navigating; skip
    }
  }
  return null
}

test('supervisor restarts after one crash and surfaces fatal on the second', async () => {
  const app = await electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
    env: { ...process.env, MARKET_ANALYSER_E2E: '1' },
  })

  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')

  // Readiness gate: any `/ohlcv` response proves the sidecar is serving auth'd
  // requests via the renderer's bearer.
  await window.waitForResponse(
    (res) => res.url().includes('/ohlcv?') && res.url().includes('127.0.0.1'),
    { timeout: 15_000 },
  )

  // Read the python child's PID and original secret (NOT the Electron main's).
  // If this comes back null the supervisor isn't exposed — MARKET_ANALYSER_E2E
  // was not set by globalSetup.
  const firstInfo = await readSupervisorInfo(app)
  expect(firstInfo, 'supervisor not exposed on globalThis').not.toBeNull()
  expect(firstInfo!.pid).not.toBeNull()
  expect(firstInfo!.pid).toBeGreaterThan(0)

  // First crash: kill the python child. Windows treats every signal as
  // TerminateProcess; macOS/Linux respect SIGKILL.
  process.kill(firstInfo!.pid!, 'SIGKILL')

  // Wait for the supervisor to respawn with a new PID and a new secret.
  const restartedInfo = await pollForChangedPid(app, firstInfo!.pid!, 30_000)
  expect(
    restartedInfo,
    'supervisor did not respawn the sidecar with a new PID within 30s',
  ).not.toBeNull()
  expect(restartedInfo!.pid).not.toBe(firstInfo!.pid)
  expect(restartedInfo!.secretToken).not.toBe(firstInfo!.secretToken)
  expect(restartedInfo!.port).toBe(firstInfo!.port)

  // Renderer-context fetch with the new secret must succeed; the old secret
  // must now 401. This proves the sidecar accepted the rotated bearer.
  const probe = await window.evaluate(
    async (info: { port: number; oldSecret: string; newSecret: string }) => {
      const start = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString()
      const end = new Date().toISOString()
      const params = new URLSearchParams({
        symbol: 'AAPL',
        timeframe: '1d',
        start,
        end,
      })
      const url = `http://127.0.0.1:${info.port}/ohlcv?${params.toString()}`
      const withNew = await fetch(url, {
        headers: { Authorization: `Bearer ${info.newSecret}` },
      })
      const withOld = await fetch(url, {
        headers: { Authorization: `Bearer ${info.oldSecret}` },
      })
      return { withNewStatus: withNew.status, withOldStatus: withOld.status }
    },
    {
      port: restartedInfo!.port,
      oldSecret: firstInfo!.secretToken,
      newSecret: restartedInfo!.secretToken,
    },
  )
  expect(probe.withNewStatus, 'post-restart fetch with new secret should be 200').toBe(200)
  expect(probe.withOldStatus, 'post-restart fetch with old secret should be 401').toBe(401)

  // Second crash: a fatal status event closes the main window and opens the
  // fatal-error window. Plan 0001 phase 4 done-when's "killing it again shows
  // the fatal-error window" bullet — never previously tested.
  process.kill(restartedInfo!.pid!, 'SIGKILL')

  const fatalDeadline = Date.now() + 30_000
  let fatalWindow: Page | null = null
  while (Date.now() < fatalDeadline) {
    const found = await findFatalWindow(app.windows())
    if (found !== null) {
      fatalWindow = found
      break
    }
    await new Promise((r) => setTimeout(r, 500))
  }
  expect(fatalWindow, 'fatal-error window never appeared after the second crash').not.toBeNull()

  // Confirm the fatal window body actually shows the user-visible message.
  await expect(fatalWindow!.locator('h2')).toContainText(/market-analyser stopped/i)

  await app.close()
})
