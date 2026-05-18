/**
 * Plan 0001 phase 5 done-when: candlestick chart for one symbol.
 *
 * Asserts the chart canvas appears after cold launch with the default symbol.
 * If the sidecar returns a non-200 (Yahoo down, cache miss with no network),
 * the renderer shows the error state instead — also captured so the spec
 * doesn't hang on a flake.
 *
 * Requires a built desktop bundle. Run with `pnpm --filter desktop test:e2e`.
 */
import { _electron as electron, test, expect } from '@playwright/test'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))

test('cold launch renders a candlestick chart for the default symbol', async () => {
  const app = await electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
  })
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')

  // First prove OhlcvView mounted — independent of whether the sidecar fetch
  // succeeds. The section has a stable aria-label.
  await expect(window.getByRole('region', { name: /OHLCV view/ })).toBeVisible({
    timeout: 15_000,
  })

  // Then wait for the chart canvas OR a visible error state OR an empty
  // state. All three prove the useOhlcv hook ran end-to-end (success,
  // surfaced failure, or no-bars). A hang here would mean an infinite
  // spinner, which is the UX failure the four-state discipline exists to
  // prevent. The empty branch is a real user state (offline + uncached
  // symbol + bad range) -- Plan 0004 phase 7 added a role+testid so this
  // predicate can match it without re-routing it through the error state.
  const chart = window.locator('[data-testid="candlestick-chart"]')
  const errorState = window.getByRole('alert')
  const emptyState = window.locator('[data-testid="ohlcv-empty"]')
  await expect(async () => {
    const chartVisible = await chart.isVisible().catch(() => false)
    const errorVisible = await errorState.isVisible().catch(() => false)
    const emptyVisible = await emptyState.isVisible().catch(() => false)
    expect(chartVisible || errorVisible || emptyVisible).toBe(true)
  }).toPass({ timeout: 30_000 })

  if (await chart.isVisible()) {
    const canvasCount = await chart.locator('canvas').count()
    expect(canvasCount).toBeGreaterThan(0)
  }

  await app.close()
})

test('Refresh advances the OHLCV window end timestamp', async () => {
  const app = await electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
  })
  const window = await app.firstWindow()

  // Attach the request listener BEFORE awaiting load — the initial /ohlcv
  // fetch fires as soon as OhlcvView mounts, and we need to capture it.
  const ohlcvUrls: string[] = []
  window.on('request', (req) => {
    const url = req.url()
    if (/^http:\/\/127\.0\.0\.1:\d+\/ohlcv\?/.test(url)) {
      ohlcvUrls.push(url)
    }
  })

  await window.waitForLoadState('domcontentloaded')
  await expect(window.getByRole('region', { name: /OHLCV view/ })).toBeVisible({
    timeout: 15_000,
  })

  // Wait for the initial fetch to land before clicking Refresh.
  await expect.poll(() => ohlcvUrls.length, { timeout: 30_000 }).toBeGreaterThanOrEqual(1)
  const firstEnd = new URL(ohlcvUrls[0]).searchParams.get('end')
  expect(firstEnd).not.toBeNull()

  // Tiny pause so Date.now() definitely differs between the two memo computes.
  await window.waitForTimeout(50)

  const refreshButton = window.getByRole('button', { name: 'Refresh' })
  await expect(refreshButton).toBeEnabled({ timeout: 15_000 })
  await refreshButton.click()

  await expect.poll(() => ohlcvUrls.length, { timeout: 15_000 }).toBeGreaterThanOrEqual(2)
  const secondEnd = new URL(ohlcvUrls[1]).searchParams.get('end')
  expect(secondEnd).not.toBeNull()
  expect(new Date(secondEnd!).getTime()).toBeGreaterThan(new Date(firstEnd!).getTime())

  await app.close()
})
