import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'
import { PERMISSION_CODES } from '../src/access/permissions'

const now = '2026-07-30T19:00:00Z'
const home = {
  id: 'home-1',
  name: 'Upland Home',
  timezone: 'America/Los_Angeles',
  currency: 'USD',
  lifecycle_state: 'active',
  is_default: true,
  revision: 1,
}

const historyResponse = {
  scope: { display_name: 'Whole Home', timezone: home.timezone },
  bucket: '15m',
  summary: {
    start_utc: '2026-07-30T18:00:00Z',
    end_utc: now,
    energy_kwh: '0.78',
    energy_cost: '0.24',
    average_power_w: '1040',
    peak_power_w: '1320',
    blended_rate_per_kwh: '0.30863',
    coverage_percent: '75',
    contributing_sensor_count: 2,
  },
  combined: [
    interval('2026-07-30T18:00:00Z', '2026-07-30T18:15:00Z', '1040', '0.26', '0.08'),
    interval('2026-07-30T18:15:00Z', '2026-07-30T18:30:00Z', null, null, null, 0),
    interval('2026-07-30T18:30:00Z', '2026-07-30T18:45:00Z', '1320', '0.33', '0.10'),
    interval('2026-07-30T18:45:00Z', now, '760', '0.19', '0.06'),
  ],
  warnings: [],
  rate_versions_used: [{ rate_plan_name: 'DOMESTIC' }],
}

function interval(
  start: string,
  end: string,
  power: string | null,
  energy: string | null,
  cost: string | null,
  contributing = 2,
) {
  return {
    interval_start_utc: start,
    interval_end_utc: end,
    average_power_w: power,
    energy_kwh: energy,
    energy_cost: cost,
    rate_per_kwh: '0.30863',
    tou_period: 'Tier 1',
    coverage_percent: contributing ? '100' : '0',
    contributing_sensor_count: contributing,
    included_sensor_count: 2,
    rate_contributions: [{ tier_name: 'Tier 1' }],
  }
}

interface Scenario {
  hasPlan: boolean
  hasLiveData: boolean
  testMode: boolean
}

async function mockServer(page: Page, historyRequests: string[], scenario: Scenario = {
  hasPlan: true,
  hasLiveData: true,
  testMode: false,
}) {
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 })
    if (path === '/api/v1/history/query') {
      historyRequests.push(route.request().postData() ?? '')
      return route.fulfill({ json: historyResponse })
    }
    const responses: Record<string, unknown> = {
      '/api/v1/auth/session': {
        authenticated: true,
        bootstrap_required: false,
        user: {
          id: 'owner-1',
          email: 'owner@example.test',
          display_name: 'Home Owner',
          roles: ['admin'],
          permissions: [...PERMISSION_CODES],
          all_sites: true,
          site_ids: [],
        },
      },
      '/api/v1/sites': [home],
      '/api/v1/appearance': {
        chart_power_color: '#78DFBF',
        chart_energy_color: '#78DFBF',
        chart_cost_color: '#C9A7FF',
        revision: 1,
        updated_at: now,
      },
      '/api/v1/devices': [
        sensor('sensor-1', 'Indoor AC', '640'),
        sensor('sensor-2', 'Outdoor AC', '400'),
      ],
      '/api/v1/utility-accounts': [],
      '/api/v1/electric-services/default/current-rate-assignment': {
        schema_version: 'current-rate-assignment/1.0',
        home_id: home.id,
        electric_service_id: null,
        assignment: null,
      },
      '/api/v1/configuration-status': {
        schema_version: 'configuration-status/1.0',
        home_id: home.id,
        state: 'ready',
        label: 'Ready',
        summary: 'Monitoring and billing are configured.',
        generated_at: now,
        issues: [],
      },
      '/api/v1/fleet/summary': {
        current_load_w: scenario.hasLiveData ? '1040' : null,
        energy_today_kwh: '0.78',
        estimated_cost_today: '0.24',
        reporting_devices: scenario.hasLiveData ? 2 : 0,
        total_devices: 2,
        online_devices: 2,
        active_alerts: 0,
        recent_peak_w: '1320',
        latest_data_at: scenario.hasLiveData ? now : null,
        latest_received_at: scenario.hasLiveData ? now : null,
        server_now: now,
        has_live_data: scenario.hasLiveData,
        has_energy_data: true,
        has_cost_data: true,
        current_rate_plan: scenario.hasPlan ? 'DOMESTIC' : null,
        current_rate_price_per_kwh: scenario.hasPlan ? '0.30863' : null,
        current_tou_bucket: scenario.hasPlan ? 'Tier 1' : null,
      },
      '/api/v1/notifications': { items: [], page: 1, page_size: 200, total: 0 },
      '/api/v1/test-mode': {
        enabled: scenario.testMode,
        remaining_seconds: scenario.testMode ? 600 : 0,
        sensor_count: scenario.testMode ? 2 : 0,
        online_sensors: scenario.testMode ? 2 : 0,
        offline_sensors: 0,
        sample_interval_seconds: 5,
        cost_preview_enabled: false,
        current_power_w: scenario.testMode ? '1500' : '0',
        total_energy_kwh: scenario.testMode ? '0.25' : '0',
        source_type: 'simulated',
        environment: 'test_mode',
        isolation: {
          real_readings: true,
          bills_and_finalized_costs: true,
          exports_and_backups: true,
          alerts: true,
          credentials_and_firmware: true,
        },
      },
    }
    const response = responses[path]
    return response === undefined
      ? route.fulfill({ json: [] })
      : route.fulfill({ json: response })
  })
}

function sensor(id: string, name: string, watts: string) {
  return {
    id,
    name,
    status: 'online_synchronized',
    current_watts: watts,
    voltage_volts: '120.4',
    current_amps: '4.20',
    frequency_hz: '60.0',
    power_factor: '0.83',
    latest_measurement_at: now,
    measurement_received_at: now,
    last_seen_at: now,
    measurement_source: 'heartbeat_live',
    measurement_freshness: 'live',
    measurement_invalid_metrics: [],
    backlog: 0,
  }
}

async function chartGeometry(page: Page) {
  return page.locator('.chart-canvas').first().evaluate((frame) => {
    const canvas = frame.querySelector('canvas')
    const frameRect = frame.getBoundingClientRect()
    const canvasRect = canvas?.getBoundingClientRect()
    return {
      frameWidth: frameRect.width,
      frameHeight: frameRect.height,
      canvasWidth: canvasRect?.width ?? 0,
      canvasHeight: canvasRect?.height ?? 0,
      scrollWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
    }
  })
}

async function expectChartFits(page: Page) {
  await expect(page.locator('.chart-canvas canvas').first()).toBeVisible()
  await page.waitForTimeout(50)
  const geometry = await chartGeometry(page)
  expect(Math.abs(geometry.frameWidth - geometry.canvasWidth)).toBeLessThanOrEqual(1)
  expect(Math.abs(geometry.frameHeight - geometry.canvasHeight)).toBeLessThanOrEqual(1)
  expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.viewportWidth + 1)
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('pm-single-home-onboarding-complete', 'true')
    localStorage.setItem('pm-theme', 'dark')
    localStorage.setItem('pm-show-daily-chart', 'true')
  })
})

test('Home charts and cards follow actual container width without content-driven blank space', async ({ page }, testInfo) => {
  const requests: string[] = []
  await mockServer(page, requests)
  await page.goto('/home')
  await expect(page.getByRole('heading', { name: 'Current pricing' })).toBeVisible()
  await expectChartFits(page)

  const initialRequests = requests.length
  for (const viewport of [
    { width: 1920, height: 1080 },
    { width: 1600, height: 900 },
    { width: 1366, height: 768 },
    { width: 1100, height: 800 },
    { width: 1024, height: 768 },
    { width: 900, height: 900 },
    { width: 768, height: 1024 },
    { width: 600, height: 900 },
    { width: 500, height: 900 },
    { width: 430, height: 932 },
    { width: 390, height: 844 },
    { width: 844, height: 390 },
    { width: 390, height: 844 },
    { width: 1920, height: 1080 },
  ]) {
    await page.setViewportSize(viewport)
    await expectChartFits(page)
    if (process.env.UPDATE_RESPONSIVE_LAYOUT_DOCS === '1' && testInfo.project.name === 'desktop' && [1920, 1024, 500, 390].includes(viewport.width)) {
      await page.screenshot({
        path: path.resolve('..', 'docs', 'screenshots', `responsive-home-${viewport.width}x${viewport.height}.png`),
        fullPage: true,
      })
    }
  }
  expect(requests.length).toBe(initialRequests)

  const expandedWidth = await page.locator('.chart-canvas').evaluate((element) => element.getBoundingClientRect().width)
  const collapse = page.getByRole('button', { name: 'Collapse navigation' })
  await expect(collapse).toHaveCount(1)
  await collapse.click()
  await expectChartFits(page)
  const collapsedWidth = await page.locator('.chart-canvas').evaluate((element) => element.getBoundingClientRect().width)
  expect(collapsedWidth).toBeGreaterThan(expandedWidth)
  if (process.env.UPDATE_RESPONSIVE_LAYOUT_DOCS === '1' && testInfo.project.name === 'desktop') {
    await page.screenshot({ path: path.resolve('..', 'docs', 'screenshots', 'responsive-home-sidebar-collapsed.png'), fullPage: true })
  }
  const expand = page.getByRole('button', { name: 'Expand navigation' })
  await expect(expand).toHaveCount(1)
  await expand.click()
  await expectChartFits(page)
  const reexpandedWidth = await page.locator('.chart-canvas').evaluate((element) => element.getBoundingClientRect().width)
  expect(Math.abs(reexpandedWidth - expandedWidth)).toBeLessThanOrEqual(1)
  if (process.env.UPDATE_RESPONSIVE_LAYOUT_DOCS === '1' && testInfo.project.name === 'desktop') {
    await page.screenshot({ path: path.resolve('..', 'docs', 'screenshots', 'responsive-home-sidebar-expanded.png'), fullPage: true })
  }

  const layout = await page.evaluate(() => {
    const hero = document.querySelector<HTMLElement>('.power-hero')
    const heroLast = hero?.lastElementChild?.getBoundingClientRect()
    const pricing = document.querySelector<HTMLElement>('.current-pricing-card')
    const pricingLast = pricing?.lastElementChild?.getBoundingClientRect()
    const side = document.querySelector<HTMLElement>('.home-side-stack')
    const sensor = document.querySelector<HTMLElement>('.sensor-health-card')
    return {
      heroBottomGap: hero && heroLast ? hero.getBoundingClientRect().bottom - heroLast.bottom : Infinity,
      pricingBottomGap: pricing && pricingLast ? pricing.getBoundingClientRect().bottom - pricingLast.bottom : Infinity,
      pricingHeight: pricing?.getBoundingClientRect().height ?? 0,
      sensorHeight: sensor?.getBoundingClientRect().height ?? 0,
      sideAlignContent: side ? getComputedStyle(side).alignContent : '',
    }
  })
  expect(layout.heroBottomGap).toBeLessThan(50)
  expect(layout.pricingBottomGap).toBeLessThan(50)
  expect(layout.pricingHeight).toBeLessThan(layout.sensorHeight)
  expect(layout.sideAlignContent).toBe('start')
})

test('Home remains content-driven with Sensor Health hidden, no plan, no live data, and Test Mode', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'The desktop worker records the scenario matrix once.')
  const requests: string[] = []
  const scenario: Scenario = { hasPlan: true, hasLiveData: true, testMode: false }
  await mockServer(page, requests, scenario)
  await page.goto('/home')
  await expectChartFits(page)

  await page.evaluate(() => { localStorage.setItem('pm-show-sensors-card', 'false') })
  await page.reload()
  await expect(page.locator('.sensor-health-card')).toHaveCount(0)
  await expectChartFits(page)
  if (process.env.UPDATE_RESPONSIVE_LAYOUT_DOCS === '1') {
    await page.screenshot({ path: path.resolve('..', 'docs', 'screenshots', 'responsive-home-sensor-health-hidden.png'), fullPage: true })
  }

  scenario.hasPlan = false
  await page.reload()
  await expect(page.getByText('Rate plan needed')).toBeVisible()
  await expectChartFits(page)
  if (process.env.UPDATE_RESPONSIVE_LAYOUT_DOCS === '1') {
    await page.screenshot({ path: path.resolve('..', 'docs', 'screenshots', 'responsive-home-no-active-plan.png'), fullPage: true })
  }

  scenario.hasLiveData = false
  await page.reload()
  await expect(page.getByText('Waiting for live data')).toBeVisible()
  await expectChartFits(page)

  scenario.testMode = true
  await page.reload()
  await expect(page.getByText('Sensor Test Mode preview')).toBeVisible()
  await expectChartFits(page)
})

test('History preserves every metric, gap, range, and axis while resizing without refetching', async ({ page }, testInfo) => {
  const requests: string[] = []
  const pageErrors: string[] = []
  page.on('pageerror', (error) => { pageErrors.push(error.message) })
  await mockServer(page, requests)
  await page.goto('/history')
  await expect(page.getByText(/1 missing interval shown as gaps/)).toBeVisible()

  const metric = page.getByLabel('Metric')
  for (const label of ['Power', 'Energy', 'Cost', 'Energy + cost']) {
    await metric.selectOption({ label })
    await expectChartFits(page)
    if (label === 'Energy + cost') {
      await expect(page.getByText(/Right scale: Estimated cost/)).toBeVisible()
    }
    if (process.env.UPDATE_RESPONSIVE_LAYOUT_DOCS === '1' && testInfo.project.name === 'desktop') {
      await page.setViewportSize({ width: 1920, height: 1080 })
      await expectChartFits(page)
      await page.screenshot({
        path: path.resolve('..', 'docs', 'screenshots', `responsive-history-${label.toLowerCase().replaceAll(' ', '-').replace('+', 'and')}.png`),
        fullPage: true,
      })
    }
  }
  const requestsAfterModes = requests.length

  for (const width of [1920, 1366, 1024, 768, 600, 390, 600, 1024, 1920]) {
    await page.setViewportSize({ width, height: width <= 600 ? 844 : 950 })
    await expectChartFits(page)
  }
  expect(requests.length).toBe(requestsAfterModes)
  await expect(page.locator('.chart-table')).toBeVisible()
  await page.locator('.chart-table summary').click()
  await expect(page.getByRole('columnheader', { name: `Exact interval (${home.timezone})` })).toBeVisible()
  await expect(page.locator('.chart-table tbody tr')).toHaveCount(4)
  await expect(page.locator('.chart-table tbody tr').first()).toContainText('Jul 30')
  expect(pageErrors).toEqual([])
})

test('repeated resize and remount cycles do not duplicate chart canvases or leak page errors', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'The desktop worker runs the 100-cycle stress matrix once.')
  const requests: string[] = []
  const pageErrors: string[] = []
  page.on('pageerror', (error) => { pageErrors.push(error.message) })
  await mockServer(page, requests)
  await page.goto('/history')
  await expectChartFits(page)
  const initialRequests = requests.length

  for (let index = 0; index < 100; index += 1) {
    const width = index % 2 === 0 ? 390 : 1440
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 })
  }
  await expectChartFits(page)
  await expect(page.locator('.chart-canvas canvas')).toHaveCount(1)
  expect(requests.length).toBe(initialRequests)

  for (const zoom of [0.8, 1, 1.25, 1.5, 2]) {
    await page.setViewportSize({
      width: Math.round(1440 / zoom),
      height: Math.round(900 / zoom),
    })
    await expectChartFits(page)
  }

  await page.goto('/home')
  await expectChartFits(page)
  await page.goto('/history')
  await expectChartFits(page)
  await expect(page.locator('.chart-canvas canvas')).toHaveCount(1)
  expect(pageErrors).toEqual([])
})
