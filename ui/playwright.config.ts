import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright config for Loca's Svelte e2e suite.
 *
 * The suite is smoke-only — fast enough to run in CI on every PR, but
 * narrow enough to not flake on model-loading or network conditions.
 * Tests mount the Vite dev server on port 5173 and stub all backend
 * routes with `page.route()` so they never hit the real FastAPI
 * server or an actual model. That keeps the suite deterministic and
 * lets it run on machines without a local Loca backend.
 *
 * See `docs/E2E.md` for how to add new tests.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  use: {
    baseURL: 'http://localhost:5173/ui',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173/ui/',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
