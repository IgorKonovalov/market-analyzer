import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  testMatch: ['**/*.spec.ts'],
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  globalSetup: './scripts/playwright-global-setup.mjs',
  use: {
    headless: false,
    trace: 'retain-on-failure',
  },
})
