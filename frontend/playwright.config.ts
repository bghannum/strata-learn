import { defineConfig, devices } from '@playwright/test'

// Real-browser E2E + visual-regression coverage, distinct from vitest.config.ts's
// jsdom-based unit tests — Phase 5.5 needs this for toHaveScreenshot() baselines
// and flows jsdom can't exercise (file upload, WebSocket-driven progress,
// multi-page navigation). See docs/design/original-project-plan.md §12 Phase 5.5.
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
  },
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
  },
  projects: [
    {
      name: 'chromium-wide',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'chromium-narrow',
      use: { ...devices['Desktop Chrome'], viewport: { width: 390, height: 844 } },
    },
  ],
})
