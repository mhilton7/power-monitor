import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 35_000,
  expect: { toHaveScreenshot: { maxDiffPixelRatio: 0.015 } },
  use: {
    baseURL: 'http://127.0.0.1:4197',
    trace: 'retain-on-failure',
    colorScheme: 'dark',
  },
  webServer: {
    command: 'npm run preview -- --port 4197',
    url: 'http://127.0.0.1:4197',
    reuseExistingServer: false,
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1000 } } },
    { name: 'desktop-light', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1000 }, colorScheme: 'light' } },
    { name: 'tablet', use: { ...devices['Desktop Chrome'], viewport: { width: 834, height: 1194 }, hasTouch: true } },
    { name: 'mobile', use: { ...devices['Pixel 7'] } },
  ],
})
