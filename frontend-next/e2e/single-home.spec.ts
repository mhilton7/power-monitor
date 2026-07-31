import { expect, test, type Page, type Route } from '@playwright/test'
import { PERMISSION_CODES } from '../src/access/permissions'

const home = {
  id: 'home-1',
  name: 'Upland Home',
  code: 'upland-home',
  timezone: 'America/Los_Angeles',
  currency: 'USD',
  locale: 'en-US',
  unit_system: 'imperial',
  allowed_cidrs: [],
  allowed_domains: [],
  allow_public_polling: false,
  lifecycle_state: 'active',
  is_default: true,
  revision: 1,
  created_at: '2026-07-24T12:00:00Z',
  updated_at: '2026-07-24T12:00:00Z',
}

async function mockServer(page: Page) {
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname
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
      '/api/v1/appearance': {
        chart_power_color: '#78DFBF',
        chart_energy_color: '#78DFBF',
        chart_cost_color: '#C9A7FF',
        revision: 1,
        updated_at: '2026-07-31T12:00:00Z',
      },
      '/api/v1/devices': [],
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
        electric_service_id: null,
        state: 'setup_needed',
        label: 'Setup needed',
        summary: '2 blocking and 0 advisory issues.',
        generated_at: '2026-07-25T12:00:00Z',
        issues: [{
          id: 'electric-service.missing',
          category: 'electric_service',
          state: 'setup_needed',
          title: 'Electric service needs setup',
          what_is_wrong: 'This home has no active electric-service record.',
          why_it_matters: 'A service is required to assign a rate plan and calculate costs.',
          how_to_fix: 'Create the electric service and confirm its billing-cycle day.',
          blocking: true,
          action: {
            id: 'electric_service.create',
            label: 'Set up electric service',
            target: '/billing?configuration=electric-service',
          },
        }],
      },
      '/api/v1/fleet/summary': {
        current_load_w: '0',
        energy_today_kwh: '0',
        estimated_cost_today: '0',
        reporting_devices: 0,
        active_alerts: 0,
        recent_peak_w: '0',
        has_live_data: false,
        has_energy_data: false,
        has_cost_data: false,
      },
      '/api/v1/notifications': { items: [], page: 1, page_size: 200, total: 0 },
      '/api/v1/admin/utility-bill-imports': [],
      '/api/v1/rates/plans': [],
      '/api/v1/admin/users': { users: [] },
      '/api/v1/admin/roles': {
        roles: [{
          id: 'viewer',
          display_name: 'Viewer',
          description: 'View home data',
          built_in: true,
          archived: false,
          revision: 1,
          permissions: ['history.view'],
          assigned_user_count: 0,
        }],
      },
      '/api/v1/admin/permissions': {
        permissions: [{
          code: 'history.view',
          group: 'Dashboard and data',
          label: 'View history',
          description: 'View historical readings.',
          high_risk: false,
        }],
      },
      '/api/v1/notification-channels': [],
      '/api/v1/notification-attempts': [],
      '/api/v1/notification-suppressions': [],
      '/api/v1/notification-history': { items: [], total: 0, page: 1, page_size: 25 },
      '/api/v1/backups': [],
      '/api/v1/exports': [],
      '/api/v1/system/health': {
        schema_version: 'system-health/1.0',
        status: 'healthy',
        checked_at: '2026-07-26T20:00:00Z',
        components: [
          { key: 'api', label: 'API', status: 'healthy', summary: 'The API is responding.', checked_at: '2026-07-26T20:00:00Z', details: {}, can_retry: true },
          { key: 'database', label: 'Database', status: 'healthy', summary: 'PostgreSQL is ready.', checked_at: '2026-07-26T20:00:00Z', details: {}, can_retry: true },
          { key: 'worker', label: 'Worker', status: 'healthy', summary: 'The worker is current.', checked_at: '2026-07-26T20:00:00Z', details: {}, can_retry: true },
          { key: 'storage', label: 'Storage', status: 'healthy', summary: 'Datasets are accessible.', checked_at: '2026-07-26T20:00:00Z', details: {}, can_retry: true },
          { key: 'backups', label: 'Backups', status: 'healthy', summary: 'The latest backup is verified.', checked_at: '2026-07-26T20:00:00Z', details: {}, can_retry: true },
          { key: 'live_data', label: 'Live data', status: 'unknown', summary: 'No real sensors are enrolled.', checked_at: '2026-07-26T20:00:00Z', details: {}, can_retry: true },
          { key: 'rate_engine', label: 'Rate engine', status: 'unknown', summary: 'No service is configured.', checked_at: '2026-07-26T20:00:00Z', details: {}, can_retry: true },
        ],
        versions: { backend: '1.0.0', frontend: '1.0.0', compatibility: 'compatible' },
        recent_events: [],
      },
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
      '/api/v1/admin/network/runtime': { ingress: 'signed_private', pull: 'disabled' },
      '/api/v1/audit-events': [],
    }
    if (path === '/api/v1/appearance' && route.request().method() === 'PUT') {
      const payload = route.request().postDataJSON() as Record<string, unknown>
      return route.fulfill({ json: { ...payload, revision: 2, updated_at: '2026-07-31T12:05:00Z' } })
    }
    const exact = data[path]
    if (exact !== undefined) return route.fulfill({ json: exact })
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 })
    return route.fulfill({ json: [] })
  })
}

test.beforeEach(async ({ page }, testInfo) => {
  await mockServer(page)
  await page.addInitScript((light) => {
    localStorage.setItem('pm-single-home-onboarding-complete', 'true')
    if (light) localStorage.setItem('pm-theme', 'light')
  }, testInfo.project.name.includes('light'))
})

test('authenticated shell top bar is flush with the viewport', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'One Chromium desktop geometry check covers the shared shell primitive.')
  await page.goto('/home')
  await expect(page.locator('.top-bar')).toBeVisible()

  const geometry = await page.evaluate(() => {
    const topBar = document.querySelector<HTMLElement>('.top-bar')
    const root = document.querySelector<HTMLElement>('#root')
    const skipLink = document.querySelector<HTMLElement>('.skip-link')
    return {
      topBarTop: topBar?.getBoundingClientRect().top,
      rootTop: root?.getBoundingClientRect().top,
      skipLinkPosition: skipLink ? getComputedStyle(skipLink).position : undefined,
    }
  })

  expect(geometry.topBarTop).toBe(0)
  expect(geometry.rootTop).toBe(0)
  expect(geometry.skipLinkPosition).toBe('fixed')
})

test('normal production routes use only the four-workspace shell', async ({ page }, testInfo) => {
  await page.goto('/home')
  const compactNavigation = testInfo.project.name === 'mobile' || testInfo.project.name === 'tablet'
  const primary = page.getByRole('navigation', { name: compactNavigation ? 'Primary mobile' : 'Primary', exact: true })
  await expect(primary.getByRole('link')).toHaveCount(4)
  await expect(primary.getByRole('link', { name: 'Home' })).toBeVisible()
  await expect(primary.getByRole('link', { name: 'History' })).toBeVisible()
  await expect(primary.getByRole('link', { name: 'Billing' })).toBeVisible()
  await expect(primary.getByRole('link', { name: 'Settings' })).toBeVisible()
  await expect(page.getByText('Connect your first sensor')).toBeVisible()
  await expect(primary.getByText('Devices')).toHaveCount(0)
  await expect(page).toHaveScreenshot('home-empty-dark.png', { fullPage: true })
})

test('Appearance applies administrator-published chart colors for every user', async ({ page }, testInfo) => {
  await page.goto('/settings/appearance')
  await expect(page.getByRole('heading', { name: 'History chart colors' })).toBeVisible()
  const powerHex = page.getByLabel('Power hexadecimal color')
  const costHex = page.getByLabel('Estimated cost hexadecimal color')
  await expect(powerHex).toHaveValue('#78DFBF')
  await powerHex.fill('#336699')
  await costHex.fill('#FF8800')
  const publishRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/appearance') && request.method() === 'PUT')
  await page.getByRole('button', { name: 'Apply colors' }).click()
  const published = await publishRequest
  expect(published.postDataJSON()).toMatchObject({ chart_power_color: '#336699', chart_cost_color: '#FF8800', expected_revision: 1 })
  await expect(page.getByText('Chart colors applied for every user.')).toBeVisible()
  const capturesAppearance = ['desktop', 'mobile', 'edge', 'firefox', 'webkit'].includes(testInfo.project.name)
  if (capturesAppearance) await expect(page).toHaveScreenshot('appearance-chart-colors-custom.png', { fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: 'Reset colors' }).click()
  await expect(powerHex).toHaveValue('#78DFBF')
  await expect(costHex).toHaveValue('#C9A7FF')
  if (capturesAppearance) await expect(page).toHaveScreenshot('appearance-chart-colors-default.png', { fullPage: true, animations: 'disabled' })
})

test('a fresh real sensor measurement stays consistent across Home and Sensor Health', async ({ page }) => {
  await page.route('**/api/v1/devices*', async (route) => {
    await route.fulfill({ json: [{
      id: 'sensor-1',
      name: 'Indoor-AC',
      status: 'online_synchronized',
      current_watts: '1.0',
      voltage_volts: '120.4',
      current_amps: '0.01',
      frequency_hz: '60.0',
      power_factor: '0.83',
      latest_measurement_at: '2026-07-29T21:55:00Z',
      measurement_received_at: '2026-07-29T21:55:02Z',
      last_seen_at: '2026-07-29T21:55:02Z',
      measurement_source: 'heartbeat_live',
      measurement_freshness: 'live',
      measurement_invalid_metrics: [],
      backlog: 0,
    }] })
  })
  await page.route('**/api/v1/fleet/summary*', async (route) => {
    await route.fulfill({ json: {
      current_load_w: '1.0',
      energy_today_kwh: '0',
      estimated_cost_today: '0',
      reporting_devices: 1,
      active_alerts: 0,
      recent_peak_w: '1.0',
      latest_data_at: '2026-07-29T21:55:00Z',
      latest_measurement_at: '2026-07-29T21:55:00Z',
      latest_received_at: '2026-07-29T21:55:02Z',
      server_now: '2026-07-29T21:55:02Z',
      has_live_data: true,
      has_energy_data: false,
      has_cost_data: false,
    } })
  })
  await page.route('**/api/v1/history/query', async (route) => {
    await route.fulfill({ json: {
      scope: { display_name: 'Whole Home' },
      summary: {
        energy_kwh: '0',
        energy_cost: '0',
        coverage_percent: '0',
        contributing_sensor_count: 0,
      },
      combined: [],
      warnings: [],
    } })
  })

  await page.goto('/home')
  await expect(page.getByText('Connected', { exact: true })).toBeVisible()
  await expect(page.getByText('1 of 1 sensors reporting')).toBeVisible()
  await expect(page.locator('.live-facts').getByText('1 W', { exact: true })).toBeVisible()
  await expect(page.locator('.power-reading').getByText('1 W', { exact: true })).toBeVisible()
  const sensor = page.locator('.sensor-health-row', { hasText: 'Indoor-AC' })
  await expect(sensor.getByText('1 W', { exact: true })).toBeVisible()
  await expect(sensor.getByText('120.4 V', { exact: true })).toBeVisible()
  await expect(sensor.getByText('0.01 A', { exact: true })).toBeVisible()
  await expect(sensor.getByText('60.0 Hz', { exact: true })).toBeVisible()
  await expect(sensor.getByText('0.83', { exact: true })).toBeVisible()
  await expect(page.getByText('Waiting for data')).toHaveCount(0)
})

test('missing history intervals remain visible gaps without crashing Home or History', async ({ page }) => {
  const pageErrors: string[] = []
  page.on('pageerror', (error) => { pageErrors.push(error.message) })
  await page.route('**/api/v1/devices*', async (route) => {
    await route.fulfill({ json: [{
      id: 'sensor-1',
      name: 'Indoor-AC',
      status: 'online_synchronized',
      current_watts: '1000',
      latest_measurement_at: '2026-07-30T18:45:00Z',
      last_seen_at: '2026-07-30T18:45:02Z',
      measurement_freshness: 'live',
      measurement_invalid_metrics: [],
      backlog: 0,
    }] })
  })
  await page.route('**/api/v1/fleet/summary*', async (route) => {
    await route.fulfill({ json: {
      current_load_w: '1000',
      energy_today_kwh: '0.55',
      estimated_cost_today: '0.17',
      reporting_devices: 1,
      total_devices: 1,
      online_devices: 1,
      active_alerts: 0,
      recent_peak_w: '1200',
      latest_data_at: '2026-07-30T18:45:00Z',
      latest_measurement_at: '2026-07-30T18:45:00Z',
      latest_received_at: '2026-07-30T18:45:02Z',
      server_now: '2026-07-30T18:45:02Z',
      has_live_data: true,
      has_energy_data: true,
      has_cost_data: false,
    } })
  })
  await page.route('**/api/v1/history/query', async (route) => {
    await route.fulfill({ json: {
      scope: { display_name: 'Whole Home', timezone: 'America/Los_Angeles' },
      bucket: '15m',
      summary: {
        start_utc: '2026-07-30T18:00:00Z',
        end_utc: '2026-07-30T18:45:00Z',
        energy_kwh: '0.55',
        energy_cost: '0.17',
        average_power_w: '733.3',
        peak_power_w: '1200',
        blended_rate_per_kwh: '0.30863',
        coverage_percent: '66.67',
        contributing_sensor_count: 1,
      },
      combined: [
        {
          interval_start_utc: '2026-07-30T18:00:00Z',
          interval_end_utc: '2026-07-30T18:15:00Z',
          average_power_w: '1000',
          energy_kwh: '0.25',
          energy_cost: '0.08',
          coverage_percent: '100',
          contributing_sensor_count: 1,
          included_sensor_count: 1,
          rate_contributions: [],
        },
        {
          interval_start_utc: '2026-07-30T18:15:00Z',
          interval_end_utc: '2026-07-30T18:30:00Z',
          average_power_w: null,
          energy_kwh: null,
          energy_cost: null,
          coverage_percent: '0',
          contributing_sensor_count: 0,
          included_sensor_count: 1,
          rate_contributions: [],
        },
        {
          interval_start_utc: '2026-07-30T18:30:00Z',
          interval_end_utc: '2026-07-30T18:45:00Z',
          average_power_w: '1200',
          energy_kwh: '0.30',
          energy_cost: '0.09',
          coverage_percent: '100',
          contributing_sensor_count: 1,
          included_sensor_count: 1,
          rate_contributions: [],
        },
      ],
      warnings: [],
      rate_versions_used: [],
    } })
  })

  await page.goto('/history')
  await expect(page.getByRole('heading', { name: 'History' })).toBeVisible()
  await expect(page.getByText(/1 missing interval shown as gaps/)).toBeVisible()
  await expect(page.locator('.chart-canvas canvas')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'This page needs attention' })).toHaveCount(0)

  await page.goto('/home')
  await expect(page.getByText(/1 missing interval shown as gaps/)).toBeVisible()
  await expect(page.locator('.chart-canvas canvas')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'This page needs attention' })).toHaveCount(0)
  expect(pageErrors).toEqual([])
})

test('legacy routes redirect without rendering a legacy page', async ({ page }) => {
  await page.goto('/devices')
  await expect(page).toHaveURL(/\/settings\/sensors/)
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Sensors' })).toBeVisible()
})

test('Billing and Settings remain usable at narrow widths', async ({ page }) => {
  await page.goto('/billing')
  await expect(page.getByRole('heading', { name: 'Billing' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Import rates from bill' })).toBeVisible()
  await page.goto('/settings')
  await expect(page.getByRole('navigation', { name: 'Settings sections' })).toBeVisible()
  await expect(page).toHaveScreenshot('settings-dark.png', { fullPage: true })
})

test('legacy route matrix resolves only to canonical workspaces', async ({ page }) => {
  const redirects: Array<{ legacy: string; canonical: string }> = [
    { legacy: '/overview', canonical: '/home' },
    { legacy: '/usage', canonical: '/history' },
    { legacy: '/costs', canonical: '/billing' },
    { legacy: '/rates', canonical: '/billing' },
    { legacy: '/administration', canonical: '/settings' },
    { legacy: '/users-access', canonical: '/settings/family' },
    { legacy: '/status-indicators', canonical: '/settings/advanced/layout' },
    { legacy: '/system-health', canonical: '/settings/advanced/system-health' },
  ]
  for (const { legacy, canonical } of redirects) {
    await page.goto(legacy)
    await expect(page).toHaveURL(new RegExp(`${canonical.replace('/', '\\/')}($|\\?)`))
  }
})

test('System Health has a canonical direct route and typed component states', async ({ page }) => {
  await page.goto('/settings/advanced/system-health')
  await expect(page).toHaveURL(/\/settings\/advanced\/system-health$/)
  await expect(page.getByRole('heading', { name: 'System health' })).toBeVisible()
  await expect(page.getByText('PostgreSQL is ready.')).toBeVisible()
  await expect(page.getByText('Not Found')).toHaveCount(0)
  const advancedBounds = await page.locator('.advanced-navigation').boundingBox()
  const healthBounds = await page.locator('.health-overall').boundingBox()
  expect(advancedBounds).not.toBeNull()
  expect(healthBounds).not.toBeNull()
  if (!advancedBounds || !healthBounds) throw new Error('Advanced and System Health panels must be rendered for spacing verification.')
  expect(healthBounds.y - (advancedBounds.y + advancedBounds.height)).toBeGreaterThanOrEqual(15)
  await expect(page).toHaveScreenshot('system-health-repaired.png', { fullPage: true })
})

test('System Health replaces the supplied generic 404 and Retry recovers', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'error-state matrix runs once')
  let attempts = 0
  await page.route('**/api/v1/system/health', async (route) => {
    attempts += 1
    if (attempts === 1) {
      return route.fulfill({
        status: 404,
        json: {
          status: 404,
          title: 'Not Found',
          detail: 'Not Found',
          code: 'not_found',
        },
      })
    }
    return route.fallback()
  })
  await page.goto('/settings/advanced/system-health')
  await expect(page.getByText('System Health service is unavailable')).toBeVisible()
  await expect(page.getByText('The settings page loaded, but the server health endpoint could not be found.')).toBeVisible()
  await expect(page.getByText('Not Found', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'View versions' }).click()
  await expect(page.getByText('Frontend commit')).toBeVisible()
  await page.getByRole('button', { name: 'Retry health check' }).click()
  await expect(page.getByRole('heading', { name: 'System health' })).toBeVisible()
  expect(attempts).toBe(2)
})

test('System Health distinguishes server, schema, and owner permission states', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'error-state matrix runs once')
  let mode: 'server' | 'schema' = 'server'
  const handler = async (route: Route) => {
    if (mode === 'server') {
      return route.fulfill({
        status: 503,
        json: {
          status: 503,
          title: 'Service unavailable',
          detail: 'diagnostic dependency unavailable',
          code: 'health_failed',
        },
      })
    }
    return route.fulfill({
      json: {
        schema_version: 'system-health/2.0',
        status: 'healthy',
        checked_at: '2026-07-26T20:00:00Z',
        components: [],
        versions: {},
        recent_events: [],
      },
    })
  }
  await page.route('**/api/v1/system/health', handler)
  await page.goto('/settings/advanced/system-health')
  await expect(page.getByText('System Health service error')).toBeVisible()
  mode = 'schema'
  await page.reload()
  await expect(page.getByText('Frontend and API versions differ')).toBeVisible()

  await page.route('**/api/v1/auth/session', async (route) => route.fulfill({
    json: {
      authenticated: true,
      bootstrap_required: false,
      user: {
        id: 'viewer-1',
        email: 'viewer@example.test',
        display_name: 'Home Viewer',
        roles: ['viewer'],
        permissions: ['overview.view'],
        all_sites: true,
        site_ids: [],
      },
    },
  }))
  await page.reload()
  await expect(page.getByText('Access denied', { exact: true })).toBeVisible()
  await expect(page.getByText('Your account does not have permission to open this workspace.')).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Primary' }).getByText('Settings')).toHaveCount(0)
})

test('System Health reports a bounded request timeout', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'bounded timeout runs once')
  await page.route('**/api/v1/system/health', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 12_000))
    await route.abort('timedout')
  })
  await page.goto('/settings/advanced/system-health')
  await expect(page.getByText('System Health request timed out')).toBeVisible({ timeout: 12_000 })
})

test('owner can enable and completely exit isolated Sensor Test Mode', async ({ page }) => {
  let enabled = false
  let sensorCount = 0
  let offlineSensorIndexes: number[] = []
  let costPreviewEnabled = false
  const sensorIds = Array.from({ length: 32 }, (_, index) => `test-sensor-${index + 1}`)
  await page.route('**/api/v1/test-mode**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    if (path === '/api/v1/test-mode/sensors') {
      return route.fulfill({ json: enabled ? Array.from({ length: sensorCount }, (_, index) => ({
        id: sensorIds[index],
        name: `Simulated Sensor ${index + 1}`,
        index: index + 1,
        online: !offlineSensorIndexes.includes(index + 1),
        current_power_w: offlineSensorIndexes.includes(index + 1) ? '0' : '900',
        energy_kwh: '0.01',
        source_type: 'simulated',
        environment: 'test_mode',
      })) : [] })
    }
    if (path === '/api/v1/test-mode/history') {
      return route.fulfill({ json: enabled ? Array.from({ length: sensorCount }, (_, index) => ({
        recorded_at: '2026-07-26T20:00:00Z',
        sensor_id: sensorIds[index],
        sensor_name: `Simulated Sensor ${index + 1}`,
        online: !offlineSensorIndexes.includes(index + 1),
        power_w: offlineSensorIndexes.includes(index + 1) ? '0' : '900',
        interval_energy_kwh: '0.01',
        source_type: 'simulated',
        environment: 'test_mode',
      })) : [] })
    }
    if (path === '/api/v1/test-mode/enable' || (path === '/api/v1/test-mode' && route.request().method() === 'PUT')) {
      const body = route.request().postDataJSON() as {
        sensor_count?: number
        offline_sensor_indexes?: number[]
        cost_preview_enabled?: boolean
      }
      enabled = true
      sensorCount = body.sensor_count ?? sensorCount
      offlineSensorIndexes = body.offline_sensor_indexes ?? offlineSensorIndexes
      costPreviewEnabled = body.cost_preview_enabled ?? costPreviewEnabled
    }
    if (path === '/api/v1/test-mode/disable') {
      enabled = false
      sensorCount = 0
      offlineSensorIndexes = []
      costPreviewEnabled = false
    }
    return route.fulfill({ json: enabled ? {
      enabled: true,
      session_id: 'test-session-1',
      site_id: 'home-1',
      started_at: '2026-07-26T20:00:00Z',
      expires_at: '2026-07-26T21:00:00Z',
      remaining_seconds: 3600,
      sensor_count: sensorCount,
      online_sensors: sensorCount - offlineSensorIndexes.length,
      offline_sensors: offlineSensorIndexes.length,
      load_profile: 'steady',
      base_load_w: '900',
      variation_percent: '0',
      sample_interval_seconds: 5,
      cost_preview_enabled: costPreviewEnabled,
      paused: false,
      current_power_w: String((sensorCount - offlineSensorIndexes.length) * 900),
      total_energy_kwh: String(sensorCount * 0.01),
      source_type: 'simulated',
      environment: 'test_mode',
      isolation: {
        real_readings: true,
        bills_and_finalized_costs: true,
        exports_and_backups: true,
        alerts: true,
        credentials_and_firmware: true,
      },
      cost_preview: {
        enabled: costPreviewEnabled,
        available: costPreviewEnabled,
        energy_kwh: String(sensorCount * 0.01),
        estimated_energy_cost: costPreviewEnabled ? '0.042' : null,
        currency: 'USD',
        disclosure: 'Temporary test-only estimate. No bill or saved cost was created.',
      },
    } : {
      enabled: false,
      remaining_seconds: 0,
      sensor_count: 0,
      online_sensors: 0,
      offline_sensors: 0,
      base_load_w: '1000',
      variation_percent: '20',
      sample_interval_seconds: 5,
      cost_preview_enabled: false,
      paused: false,
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
    } })
  })
  await page.goto('/settings/advanced/sensor-test-mode')
  await page.getByLabel('Simulated active sensors').fill('3')
  await page.getByLabel('Simulate offline sensors').fill('2')
  await page.getByText('Temporary current-rate cost preview').click()
  await page.getByRole('button', { name: 'Enable Sensor Test Mode' }).click()
  await expect(page.getByRole('status', { name: 'Sensor Test Mode is active' })).toBeVisible()
  await expect(page.getByText('2/3 simulated sensors')).toBeVisible()
  await page.goto('/home')
  await expect(page.getByText('Sensor Test Mode preview')).toBeVisible()
  await expect(page.getByText('2/3', { exact: true })).toBeVisible()
  await page.goto('/history')
  await expect(page.getByRole('heading', { name: 'Test Mode history' })).toBeVisible()
  await expect(page.getByText('Simulated Sensor 1')).toBeVisible()
  await page.goto('/settings/sensors')
  await expect(page.getByRole('heading', { name: 'Simulated sensors' })).toBeVisible()
  await expect(page.getByText('Simulated Sensor 3')).toBeVisible()
  await page.goto('/billing')
  await expect(page.getByRole('heading', { name: 'Test Mode cost preview' })).toBeVisible()
  await expect(page.getByText('$0.04')).toBeVisible()
  await page.goto('/settings/advanced/sensor-test-mode')
  await page.getByLabel('Simulated active sensors').fill('5')
  await page.getByLabel('Simulate offline sensors').fill('2')
  await page.getByRole('button', { name: 'Apply test settings' }).click()
  await expect(page.getByText('4/5 simulated sensors')).toBeVisible()
  expect(sensorIds[0]).toBe('test-sensor-1')
  await page.getByRole('button', { name: 'Exit test mode' }).click()
  await expect(page.getByRole('status', { name: 'Sensor Test Mode is active' })).toHaveCount(0)
  await page.goto('/history')
  await expect(page.getByRole('heading', { name: 'Test Mode history' })).toHaveCount(0)
})

test('page boundaries keep configuration out of Home and History', async ({ page }) => {
  await page.goto('/home')
  await expect(page.getByRole('heading', { name: 'Home settings' })).toHaveCount(0)
  await expect(page.getByText('Advanced Rate Settings')).toHaveCount(0)
  await page.goto('/history')
  await expect(page.getByText('Advanced Rate Settings')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Add sensor/i })).toHaveCount(0)
  await page.goto('/settings/notifications')
  await expect(page.getByRole('heading', { name: 'Notifications' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Appearance' })).toHaveCount(0)
})

test('advanced permissions can create a validated custom role', async ({ page }) => {
  let createdRole: Record<string, unknown> | undefined
  await page.route('**/api/v1/admin/roles', async (route) => {
    if (route.request().method() === 'POST') {
      createdRole = route.request().postDataJSON() as Record<string, unknown>
      return route.fulfill({ status: 201, json: { id: 'custom-history', ...createdRole } })
    }
    return route.fallback()
  })
  await page.goto('/settings/advanced')
  await page.getByRole('button', { name: 'Permissions & audit' }).click()
  await page.getByRole('button', { name: 'New custom role' }).click()
  await page.getByLabel('Role name').fill('History reviewer')
  await page.getByLabel('Description').fill('Reviews home history')
  await page.getByText('View history', { exact: true }).click()
  await page.getByRole('button', { name: 'Save role' }).click()
  await expect.poll(() => createdRole).toMatchObject({
    display_name: 'History reviewer',
    permissions: ['history.view'],
  })
})

test('Home has a stable loading state', async ({ page }) => {
  await page.route('**/api/v1/fleet/summary*', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1_500))
    await route.fulfill({ json: { has_live_data: false } })
  })
  await page.goto('/home')
  await expect(page.getByText('Preparing your home…')).toBeVisible()
  await expect(page).toHaveScreenshot('home-loading.png', { fullPage: true })
})

test('Home has a recoverable server error state', async ({ page }) => {
  await page.route('**/api/v1/fleet/summary*', async (route) => {
    await route.fulfill({
      status: 503,
      contentType: 'application/problem+json',
      body: JSON.stringify({ title: 'Home summary unavailable', detail: 'The server could not prepare this home yet.' }),
    })
  })
  await page.goto('/home')
  await expect(page.getByText('The server could not prepare this home yet.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible()
  await expect(page).toHaveScreenshot('home-error.png', { fullPage: true })
})

test('fresh deployment completes the skippable Single Home onboarding path', async ({ page }) => {
  await page.unroute('**/api/v1/**')
  let createdHome: typeof home | undefined
  let serviceCreated = false
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    if (path === '/api/v1/auth/session') {
      return route.fulfill({ json: {
        authenticated: true,
        bootstrap_required: false,
        user: { id: 'owner-1', email: 'owner@example.test', display_name: 'Home Owner', roles: ['admin'], permissions: [...PERMISSION_CODES], all_sites: true, site_ids: [] },
      } })
    }
    if (path === '/api/v1/sites' && route.request().method() === 'POST') {
      createdHome = home
      return route.fulfill({ status: 201, json: createdHome })
    }
    if (path === '/api/v1/sites') return route.fulfill({ json: createdHome ? [createdHome] : [] })
    if (path === '/api/v1/utility-accounts' && route.request().method() === 'POST') {
      serviceCreated = true
      return route.fulfill({ status: 201, json: { id: 'service-1', name: 'Home electric service' } })
    }
    if (path === '/api/v1/utility-accounts') {
      return route.fulfill({ json: serviceCreated ? [{
        id: 'service-1', site_id: 'home-1', name: 'Home electric service', status: 'active',
        timezone: home.timezone, currency: 'USD', billing_cycle_start_day: 1,
      }] : [] })
    }
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 })
    if (path === '/api/v1/fleet/summary') return route.fulfill({ json: { has_live_data: false } })
    return route.fulfill({ json: [] })
  })
  await page.goto('/home')
  await expect(page).toHaveURL(/\/onboarding$/)
  await page.getByRole('button', { name: 'Get started' }).click()
  await page.getByLabel('What should we call this home?').fill('Upland Home')
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByRole('button', { name: 'Save home' }).click()
  await expect(page.getByRole('heading', { name: 'Select utility' })).toBeVisible()
  await page.getByRole('button', { name: 'Create electric service' }).click()
  await page.getByRole('button', { name: 'Skip for now' }).click()
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByRole('button', { name: 'Skip for now' }).click()
  await page.getByRole('button', { name: 'Finish setup' }).click()
  await page.getByRole('button', { name: 'Go to Home' }).click()
  await expect(page).toHaveURL(/\/home$/)
  await expect(page.getByRole('heading', { name: /^Good (morning|afternoon|evening), Home$/ })).toBeVisible()
})
