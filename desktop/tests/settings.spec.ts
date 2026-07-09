/**
 * Plan 0006 phase 5 done-when (e2e): reveal → copy → rotate → 401-on-old-bearer.
 *
 * Each expect(...) line defends a specific behavioral claim from the plan:
 *  - Settings page renders the MCP endpoint URL with the live sidecar port.
 *  - The secret is hidden on initial mount (placeholder dots only).
 *  - Clicking Reveal puts the plaintext secret in the rendered DOM.
 *  - Clicking Copy writes the secret to the OS clipboard (verified via the
 *    Electron clipboard module — `navigator.clipboard` in the renderer
 *    delegates to the OS clipboard, which Playwright reads back via
 *    app.evaluate). On headless CI without clipboard permission this falls
 *    back to asserting the in-DOM "Copied!" feedback.
 *  - Clicking Rotate calls the sidecar's rotate endpoint, the page now shows
 *    a different secret, AND a fresh HTTP POST to /mcp using the old bearer
 *    returns 401 — the rotation-invalidation contract under e2e conditions.
 *
 * Requires a built desktop bundle. Run with `pnpm --filter desktop test:e2e`.
 */
import { _electron as electron, expect, test } from '@playwright/test'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))

test('reveal → copy → rotate → old bearer is rejected by /mcp', async () => {
  const app = await electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
  })
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')

  // Navigate to Settings.
  await expect(window.getByTestId('nav-settings')).toBeVisible({ timeout: 15_000 })
  await window.getByTestId('nav-settings').click()

  // Endpoint URL is rendered with the live sidecar port (not '<loading>').
  const endpointInput = window.getByLabel('Endpoint URL')
  await expect(endpointInput).toBeVisible({ timeout: 15_000 })
  await expect(endpointInput).toHaveValue(/http:\/\/127\.0\.0\.1:\d+\/mcp$/, { timeout: 15_000 })
  const endpointUrl = await endpointInput.inputValue()
  const portMatch = endpointUrl.match(/127\.0\.0\.1:(\d+)/)
  expect(portMatch).not.toBeNull()
  const port = Number(portMatch![1])

  // Secret is hidden on mount: the plaintext element does not exist yet, only
  // the placeholder (which is aria-hidden so screen readers skip it too).
  await expect(window.getByTestId('mcp-secret-hidden')).toBeVisible({ timeout: 15_000 })
  await expect(window.getByTestId('mcp-secret-hidden')).toHaveAttribute('aria-hidden', 'true')
  await expect(window.getByTestId('mcp-secret-plaintext')).toHaveCount(0)

  // Reveal → plaintext appears.
  await window.getByTestId('mcp-secret-reveal').click()
  const plaintext = window.getByTestId('mcp-secret-plaintext')
  await expect(plaintext).toBeVisible()
  const originalSecret = (await plaintext.textContent()) ?? ''
  expect(originalSecret).toMatch(/^[0-9a-f]{64}$/)

  // Copy → clipboard receives the secret. Reading the OS clipboard goes
  // through Electron's main process via app.evaluate.
  await window.getByTestId('mcp-secret-copy').click()
  await expect(window.getByTestId('mcp-secret-copy')).toHaveText('Copied!', { timeout: 5_000 })
  const clipboardContents = await app.evaluate(({ clipboard }) => clipboard.readText())
  // On headless CI without clipboard access the clipboard may be empty;
  // accept either the secret or an empty string but never something else
  // (which would mean the renderer wrote the wrong value).
  expect(['', originalSecret]).toContain(clipboardContents)

  // Rotate → page updates to the new secret.
  await window.getByTestId('mcp-secret-rotate').click()
  await expect(plaintext).not.toHaveText(originalSecret, { timeout: 10_000 })
  const newSecret = (await plaintext.textContent()) ?? ''
  expect(newSecret).toMatch(/^[0-9a-f]{64}$/)
  expect(newSecret).not.toBe(originalSecret)

  // Old bearer must 401 against /mcp on the next request — the live-rotation
  // contract. Done outside the renderer so we test the sidecar surface
  // directly without bouncing through the typed fetch client.
  const oldBearerResponse = await fetch(`http://127.0.0.1:${port}/mcp`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${originalSecret}`,
      'content-type': 'application/json',
    },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }),
  })
  expect(oldBearerResponse.status).toBe(401)

  // New bearer must authenticate — proves the rotation succeeded and the new
  // bearer is now the live one. Assert membership in the expected non-error
  // set rather than a bare `not.toBe(401)`: this bare `tools/list` POST omits
  // the MCP session/Accept negotiation, so the auth-passed response is a 200
  // or a 400/406 from the MCP protocol layer — never a 401 (auth) or a 5xx.
  const newBearerResponse = await fetch(`http://127.0.0.1:${port}/mcp`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${newSecret}`,
      'content-type': 'application/json',
    },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }),
  })
  expect([200, 400, 406]).toContain(newBearerResponse.status)

  await app.close()
})

test('toggling between Chart and Settings preserves Chart state', async () => {
  const app = await electron.launch({
    args: [join(__dirname, '..', 'dist', 'main', 'index.cjs')],
  })
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')

  await expect(window.getByRole('region', { name: /OHLCV view/ })).toBeVisible({ timeout: 15_000 })
  await window.getByTestId('nav-settings').click()
  await expect(window.getByText('MCP access')).toBeVisible({ timeout: 15_000 })
  await expect(window.getByRole('region', { name: /OHLCV view/ })).toHaveCount(0)

  await window.getByTestId('nav-chart').click()
  await expect(window.getByRole('region', { name: /OHLCV view/ })).toBeVisible({ timeout: 15_000 })
  await expect(window.getByText('MCP access')).toHaveCount(0)

  await app.close()
})
