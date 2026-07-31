import { defineConfig, devices } from '@playwright/test'

const externalBaseUrl = process.env.PW_EXTERNAL_BASE_URL

export default defineConfig({
  testDir: './e2e',
  timeout: 35_000,
  workers: process.platform === 'win32' ? 4 : undefined,
  expect: { toHaveScreenshot: { maxDiffPixelRatio: 0.015 } },
  use: {
    baseURL: externalBaseUrl ?? 'http://127.0.0.1:4197',
    trace: 'retain-on-failure',
    colorScheme: 'dark',
    ignoreHTTPSErrors: true,
  },
  webServer: externalBaseUrl ? undefined : {
    command: 'npm run preview -- --port 4197',
    url: 'http://127.0.0.1:4197',
    reuseExistingServer: false,
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1000 } } },
    { name: 'desktop-light', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1000 }, colorScheme: 'light' } },
    { name: 'tablet', use: { ...devices['Desktop Chrome'], viewport: { width: 834, height: 1194 }, hasTouch: true } },
    { name: 'mobile', use: { ...devices['Pixel 7'] } },
    {
      name: 'edge',
      grep: /Viewer receives|Appearance exposes|History preserves/,
      use: { ...devices['Desktop Chrome'], channel: 'msedge', viewport: { width: 1440, height: 1000 } },
    },
    { name: 'firefox', use: { ...devices['Desktop Firefox'], viewport: { width: 1440, height: 1000 } } },
    { name: 'webkit', use: { ...devices['Desktop Safari'], viewport: { width: 1440, height: 1000 } } },
  ],
})
