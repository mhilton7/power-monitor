import { defineConfig, devices } from '@playwright/test'

const externalBaseUrl = process.env.PW_EXTERNAL_BASE_URL

export default defineConfig({
  testDir: './e2e',
  testMatch: 'single-pass-repair.spec.ts',
  timeout: 45_000,
  expect: { toHaveScreenshot: { maxDiffPixelRatio: 0.015 } },
  use: {
    baseURL: externalBaseUrl ?? 'http://127.0.0.1:4198',
    trace: 'retain-on-failure',
    colorScheme: 'dark',
    ignoreHTTPSErrors: true,
  },
  webServer: externalBaseUrl ? undefined : {
    command: 'npm run preview -- --port 4198',
    url: 'http://127.0.0.1:4198',
    reuseExistingServer: false,
  },
  projects: [
    { name: 'repair-3440x1440', use: { ...devices['Desktop Chrome'], viewport: { width: 3440, height: 1440 } } },
    { name: 'repair-1920x1080', use: { ...devices['Desktop Chrome'], viewport: { width: 1920, height: 1080 } } },
    { name: 'repair-768x1024', use: { ...devices['Desktop Chrome'], viewport: { width: 768, height: 1024 }, hasTouch: true } },
    { name: 'repair-390x844', use: { ...devices['Pixel 7'], viewport: { width: 390, height: 844 } } },
  ],
})
