import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  testMatch: ['**/*.spec.ts'],
  // `tests/main/` houses Jest unit tests for the Electron main process; they
  // are NOT Playwright e2e specs and would fail under Playwright's harness.
  // `pnpm test:main` runs them via jest with `jest.config.main.ts`.
  testIgnore: ['**/tests/main/**'],
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  globalSetup: './scripts/playwright-global-setup.mjs',
  use: {
    headless: false,
    trace: 'retain-on-failure',
  },
})
