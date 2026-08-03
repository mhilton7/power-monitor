import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'
import { PERMISSION_CODES } from '../src/access/permissions'

const home = {
  id: 'home-1',
  name: 'Upland Home',
  timezone: 'America/Los_Angeles',
  currency: 'USD',
  lifecycle_state: 'active',
  is_default: true,
  revision: 1,
}

function sensor(
  id: string,
  name: string,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    id,
    name,
    status: 'online_synchronized',
    current_watts: '0.8',
    voltage_volts: '116.3',
    current_amps: '0.00',
    frequency_hz: '60.0',
    power_factor: '0.00',
    latest_measurement_at: new Date().toISOString(),
    measurement_received_at: new Date().toISOString(),
    last_seen_at: new Date().toISOString(),
    measurement_source: 'heartbeat_live',
    measurement_freshness: 'live',
    measurement_invalid_metrics: [],
    backlog: 0,
    ...overrides,
  }
}

async function mockHome(
  page: Page,
  sensors: Record<string, unknown>[],
  fleetOverrides: Record<string, unknown> = {},
) {
  await page.route('**/api/v1/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname
    if (pathname === '/api/v1/events/stream') return route.fulfill({ status: 204 })
    const data: Record<string, unknown> = {
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
      '/api/v1/devices': sensors,
      '/api/v1/utility-accounts': [],
      '/api/v1/configuration-status': {
        schema_version: 'configuration-status/1.0',
        home_id: home.id,
        state: 'waiting_for_data',
        label: 'Waiting for data',
        summary: 'The sensor is connected.',
        generated_at: new Date().toISOString(),
        issues: [],
      },
      '/api/v1/electric-services/default/current-rate-assignment': {
        schema_version: 'current-rate-assignment/1.0',
        home_id: home.id,
        electric_service_id: null,
        assignment: null,
      },
      '/api/v1/fleet/summary': {
        current_load_w: '0.8',
        energy_today_kwh: '0',
        estimated_cost_today: '0',
        reporting_devices: 1,
        active_alerts: 0,
        recent_peak_w: '0.8',
        latest_data_at: new Date().toISOString(),
        latest_received_at: new Date().toISOString(),
        server_now: new Date().toISOString(),
        has_live_data: true,
        has_energy_data: false,
        has_cost_data: false,
        ...fleetOverrides,
      },
      '/api/v1/notifications': { items: [], page: 1, page_size: 200, total: 0 },
      '/api/v1/test-mode': {
        enabled: false,
        remaining_seconds: 0,
        sensor_count: 0,
        online_sensors: 0,
        offline_sensors: 0,
        sample_interval_seconds: 5,
        cost_preview_enabled: false,
        current_power_w: '0',
        total_energy_kwh: '0',
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
    const response = data[pathname]
    return response === undefined
      ? route.fulfill({ json: [] })
      : route.fulfill({ json: response })
  })
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('pm-single-home-onboarding-complete', 'true')
    localStorage.setItem('pm-theme', 'dark')
    localStorage.setItem('pm-show-daily-chart', 'false')
  })
})

test('Sensor Health renders one compact accessible measurement strip per sensor', async ({ page }, testInfo) => {
  await mockHome(page, [
    sensor('sensor-1', 'Indoor-AC'),
    sensor('sensor-2', 'Garage', {
      status: 'api_healthy_meter_failed',
      current_watts: null,
      power_factor: null,
      voltage_volts: '119.8',
      measurement_freshness: 'invalid',
      measurement_invalid_metrics: ['power_watts', 'power_factor'],
      latest_measurement_at: '2023-07-29T21:55:00Z',
    }),
  ])
  await page.goto('/home')

  const card = page.locator('.home-side-stack .surface', { hasText: 'Sensor health' })
  await expect(card).toBeVisible()
  await expect(card.locator('.sensor-health-row')).toHaveCount(2)
  await expect(card.locator('.sensor-electrical-strip')).toHaveCount(2)
  await expect(card.locator('.sensor-electrical-metric')).toHaveCount(10)
  await expect(card.locator('.sensor-electrical-grid')).toHaveCount(0)
  await expect(card.getByText('Invalid reading')).toHaveCount(0)
  await expect(card.getByLabel('Power measurement invalid')).toHaveText('—')
  await expect(card.getByLabel('Power factor measurement invalid')).toHaveText('—')
  await expect(card.getByLabel('Current, 0.00 A')).toHaveCount(2)
  await expect(card.getByText(/Received \d+s ago/)).toHaveCount(2)
  await expect(page.getByText('Sensor data last received', { exact: true })).toHaveCount(0)
  await expect(card.getByRole('link', { name: 'Manage' })).toHaveCSS('white-space', 'nowrap')
  await expect(card).toHaveScreenshot('sensor-health-compact-multiple.png')

  if (process.env.UPDATE_SENSOR_HEALTH_DOCS === '1' && testInfo.project.name === 'desktop') {
    await card.screenshot({
      path: path.resolve('..', 'docs', 'screenshots', 'sensor-health-after-desktop.png'),
    })
  }
})

test('Home displays the combined fleet value while preserving per-sensor readings', async ({ page }) => {
  await mockHome(
    page,
    [
      sensor('sensor-1', 'Indoor-AC', { current_watts: '1.1' }),
      sensor('sensor-2', 'Outdoor-AC', { current_watts: '1.0' }),
    ],
    {
      current_load_w: '2.1',
      recent_peak_w: '2.1',
      reporting_devices: 2,
    },
  )
  await page.goto('/home')

  await expect(page.locator('.power-reading strong')).toHaveText('2.1 W')
  const health = page.locator('.sensor-health-card')
  await expect(health.locator('.sensor-health-row', { hasText: 'Indoor-AC' })).toContainText('1.1 W')
  await expect(health.locator('.sensor-health-row', { hasText: 'Outdoor-AC' })).toContainText('1 W')
  await expect(page.getByText('Sensor data last received', { exact: true })).toHaveCount(0)
})

test('one-sensor Sensor Health remains compact without horizontal overflow', async ({ page }, testInfo) => {
  await mockHome(page, [sensor('sensor-1', 'Indoor-AC')])
  await page.goto('/home')

  const card = page.locator('.home-side-stack .surface', { hasText: 'Sensor health' })
  await expect(card.locator('.sensor-health-row')).toHaveCount(1)
  await expect(card.locator('.sensor-electrical-strip')).toHaveCount(1)
  await expect(card).toHaveScreenshot('sensor-health-compact-single.png')
  const geometry = await card.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }))
  expect(geometry.scrollWidth - geometry.clientWidth).toBeLessThanOrEqual(1)
  await expect(page.getByText('0.8 W')).toHaveCount(4)
  await expect(page.getByText('1 W', { exact: true })).toHaveCount(0)

  if (process.env.UPDATE_SENSOR_HEALTH_DOCS === '1' && testInfo.project.name === 'mobile') {
    await card.screenshot({
      path: path.resolve('..', 'docs', 'screenshots', 'sensor-health-after-mobile.png'),
    })
  }
  if (process.env.UPDATE_BACKUP_LIVE_DOCS === '1' && testInfo.project.name === 'desktop') {
    await page.screenshot({
      path: path.resolve('..', 'docs', 'screenshots', 'home-live-receipt-precision.png'),
      fullPage: true,
    })
  }
})

test('one shared local timer advances header and sensor receipt ages without one-second requests', async ({ page }) => {
  const now = Date.now()
  const receivedAt = new Date(now - 1_000).toISOString()
  const requests: string[] = []
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/')) requests.push(request.url())
  })
  await mockHome(page, [
    sensor('sensor-1', 'Indoor-AC', {
      latest_measurement_at: receivedAt,
      measurement_received_at: receivedAt,
      last_seen_at: receivedAt,
    }),
  ])
  await page.goto('/home')

  const headerAge = page.locator('.live-facts .freshness')
  const sensorAge = page.locator('.sensor-health-row', { hasText: 'Indoor-AC' })
  await expect(headerAge).toContainText(/\d+s ago/)
  await expect(sensorAge).toContainText(/Received \d+s ago/)
  const initialHeaderSeconds = elapsedSeconds(await headerAge.innerText())
  const initialSensorSeconds = elapsedSeconds(await sensorAge.innerText())
  const requestCount = requests.length
  await expect
    .poll(
      async () => {
        const laterHeaderSeconds = elapsedSeconds(await headerAge.innerText())
        const laterSensorSeconds = elapsedSeconds(await sensorAge.innerText())
        return Math.min(
          laterHeaderSeconds - initialHeaderSeconds,
          laterSensorSeconds - initialSensorSeconds,
        )
      },
      { timeout: 5_000 },
    )
    .toBeGreaterThan(0)
  // A normal bounded fallback refresh may cross this assertion when a heavily
  // loaded cross-browser run delays the worker. What must never happen is a
  // request on every local one-second age tick.
  expect(requests.length - requestCount).toBeLessThanOrEqual(1)
})

test('compact strips remain within two lines across the required viewport and zoom matrix', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'The desktop worker executes the explicit matrix once.')
  await mockHome(page, [
    sensor('sensor-1', 'Indoor-AC'),
    sensor('sensor-2', 'Garage', {
      current_watts: '0',
      voltage_volts: '119.8',
      power_factor: '0.83',
    }),
  ])
  await page.goto('/home')

  const cases = [
    { width: 1920, height: 1080, zoom: 1 },
    { width: 1366, height: 768, zoom: 1 },
    { width: 768, height: 1024, zoom: 1 },
    { width: 390, height: 844, zoom: 1 },
    { width: 1920, height: 1080, zoom: 2 },
  ]
  for (const layout of cases) {
    await page.setViewportSize({
      width: Math.round(layout.width / layout.zoom),
      height: Math.round(layout.height / layout.zoom),
    })
    const result = await page.evaluate(() => {
      const strips = [...document.querySelectorAll<HTMLElement>('.sensor-electrical-strip')]
      const lineCounts = strips.map((strip) => {
        const tops = [...strip.querySelectorAll<HTMLElement>('.sensor-electrical-metric')]
          .map((metric) => Math.round(metric.getBoundingClientRect().top))
        return new Set(tops).size
      })
      return {
        pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        stripOverflow: strips.map((strip) => strip.scrollWidth - strip.clientWidth),
        lineCounts,
      }
    })
    expect(
      result.pageOverflow,
      `page overflow at ${layout.width}x${layout.height}, ${layout.zoom * 100}%`,
    ).toBeLessThanOrEqual(1)
    expect(
      Math.max(...result.stripOverflow),
      `strip overflow at ${layout.width}x${layout.height}, ${layout.zoom * 100}%`,
    ).toBeLessThanOrEqual(1)
    expect(
      Math.max(...result.lineCounts),
      `metric lines at ${layout.width}x${layout.height}, ${layout.zoom * 100}%`,
    ).toBeLessThanOrEqual(2)
  }
})

function elapsedSeconds(text: string): number {
  const match = text.match(/(\d+)s ago/)
  if (!match) throw new Error(`Expected a seconds-level elapsed label, received: ${text}`)
  return Number(match[1])
}
