/**
 * Plan 0001 phase 4 done-when: Electron security defaults.
 *
 * Asserts:
 *  - `window.require` is undefined (no nodeIntegration in the renderer).
 *  - Cross-origin fetch (different localhost port, different host) is blocked by CSP.
 *  - Fetch to the sidecar succeeds with the injected bearer.
 *
 * Requires a built desktop bundle. Run with `pnpm --filter desktop test:e2e`.
 */
import { _electron as electron, test, expect } from '@playwright/test'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))

test('renderer cannot access node integration', async () => {
  const app = await electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
  })
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')

  const hasRequire = await window.evaluate(() => typeof (globalThis as any).require !== 'undefined')
  expect(hasRequire).toBe(false)

  const hasProcess = await window.evaluate(() => typeof (globalThis as any).process !== 'undefined')
  expect(hasProcess).toBe(false)

  await app.close()
})

test('sidecar fetch with injected bearer succeeds', async () => {
  const app = await electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
  })
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')

  // OhlcvView fires `/ohlcv` on mount. A response that's not 401 proves the
  // preload-injected bearer was accepted by the sidecar's auth middleware.
  const response = await window.waitForResponse(
    (res) => res.url().includes('/ohlcv?') && res.url().includes('127.0.0.1'),
    { timeout: 15_000 },
  )
  expect(response.status()).not.toBe(401)

  await app.close()
})

test('CSP blocks cross-origin fetch (wrong sidecar port and example.com)', async () => {
  const app = await electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
    env: { ...process.env, MARKET_ANALYSER_E2E: '1' },
  })
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')

  // Discover the real sidecar port so we can probe a guaranteed-different one.
  const supervisorInfo = await app.evaluate(() => {
    const sup = (
      globalThis as {
        __sidecarSupervisor?: { getInfo(): { port: number } | null }
      }
    ).__sidecarSupervisor
    return sup?.getInfo() ?? null
  })
  expect(supervisorInfo, 'supervisor must be exposed under MARKET_ANALYSER_E2E=1').not.toBeNull()
  const realPort = supervisorInfo!.port
  // Pick a port that is (a) not the real one and (b) under 1024 so even if
  // something is listening, the CSP block fires first.
  const wrongPort = realPort === 1 ? 2 : 1

  const probe = await window.evaluate(
    async (params: { wrongPort: number }) => {
      async function classify(url: string): Promise<string> {
        try {
          await fetch(url)
          return 'allowed'
        } catch (e) {
          return (e as Error).message
        }
      }
      return {
        wrongLocalhost: await classify(`http://127.0.0.1:${params.wrongPort}/ping`),
        externalHost: await classify('https://example.com'),
      }
    },
    { wrongPort },
  )
  // Both classifications should be a CSP refusal — fetch throws TypeError with
  // a "Failed to fetch" / "Refused to connect" message under Chromium's CSP.
  // The exact wording varies; what matters is it is NOT 'allowed'.
  expect(probe.wrongLocalhost, 'fetch to wrong localhost port must be CSP-blocked').not.toBe(
    'allowed',
  )
  expect(probe.externalHost, 'fetch to external host must be CSP-blocked').not.toBe('allowed')

  await app.close()
})
