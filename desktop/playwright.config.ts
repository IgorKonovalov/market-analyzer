import { defineConfig } from '@playwright/test'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  testDir: './tests',
  testMatch: ['**/*.spec.ts'],
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  globalSetup: join(__dirname, 'scripts', 'playwright-global-setup.mjs'),
  use: {
    headless: false,
    trace: 'retain-on-failure',
  },
})
