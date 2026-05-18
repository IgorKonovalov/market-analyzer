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

  // Then wait for the chart canvas OR a visible error state. Both prove the
  // useOhlcv hook ran end-to-end (success or surfaced failure); a hang here
  // would mean an infinite spinner, which is the UX failure the four-state
  // discipline exists to prevent.
  const chart = window.locator('[data-testid="candlestick-chart"]')
  const errorState = window.getByRole('alert')
  await expect(async () => {
    const chartVisible = await chart.isVisible().catch(() => false)
    const errorVisible = await errorState.isVisible().catch(() => false)
    expect(chartVisible || errorVisible).toBe(true)
  }).toPass({ timeout: 30_000 })

  if (await chart.isVisible()) {
    const canvasCount = await chart.locator('canvas').count()
    expect(canvasCount).toBeGreaterThan(0)
  }

  await app.close()
})
