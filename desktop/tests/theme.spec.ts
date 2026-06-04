/**
 * Plan 0033 phase 3 done-when (e2e): the Settings Appearance control changes the
 * live theme and the choice survives a reload.
 *
 * Each expect(...) defends a behavioral claim from the plan:
 *  - Selecting Light then Dark flips `html[data-theme]` to `dark`.
 *  - A representative computed color (body background) actually changes between
 *    Light and Dark — proving the token override reaches paint, not just the
 *    attribute.
 *  - After a full reload the choice is restored from localStorage by the
 *    pre-paint bootstrap (no flash, no reset to system).
 *
 * Requires a built desktop bundle. Run with `pnpm --filter desktop test:e2e`.
 */
import { _electron as electron, expect, test } from '@playwright/test'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))

test('Appearance control changes the theme and the choice persists across reload', async () => {
  const app = await electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
  })
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')

  // Navigate to Settings.
  await expect(window.getByTestId('nav-settings')).toBeVisible({ timeout: 15_000 })
  await window.getByTestId('nav-settings').click()
  await expect(window.getByTestId('theme-option-dark')).toBeVisible({ timeout: 15_000 })

  // Pin Light first so the comparison is deterministic regardless of the OS
  // preference the test host happens to run under.
  await window.getByTestId('theme-option-light').click()
  await expect
    .poll(() => window.evaluate(() => document.documentElement.dataset.theme ?? null))
    .toBe('light')
  const lightBg = await window.evaluate(() => getComputedStyle(document.body).backgroundColor)

  // Select Dark → attribute flips AND the body background actually changes.
  await window.getByTestId('theme-option-dark').click()
  await expect
    .poll(() => window.evaluate(() => document.documentElement.dataset.theme ?? null))
    .toBe('dark')
  const darkBg = await window.evaluate(() => getComputedStyle(document.body).backgroundColor)
  expect(darkBg).not.toBe(lightBg)

  // Reload: the pre-paint bootstrap must restore the Dark choice from
  // localStorage rather than reverting to system.
  await window.reload()
  await window.waitForLoadState('domcontentloaded')
  await expect
    .poll(() => window.evaluate(() => document.documentElement.dataset.theme ?? null), {
      timeout: 15_000,
    })
    .toBe('dark')
  const reloadedBg = await window.evaluate(() => getComputedStyle(document.body).backgroundColor)
  expect(reloadedBg).toBe(darkBg)

  await app.close()
})
