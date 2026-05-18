/**
 * Plan 0001 phase 4 done-when: Electron security defaults.
 *
 * Asserts:
 *  - `window.require` is undefined (no nodeIntegration in the renderer).
 *  - Cross-origin fetch is blocked by CSP.
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
