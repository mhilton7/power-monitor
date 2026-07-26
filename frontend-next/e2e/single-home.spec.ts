import { expect, test, type Page } from '@playwright/test'

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
          permissions: ['roles.view', 'roles.manage'],
          all_sites: true,
          site_ids: [],
        },
      },
      '/api/v1/sites': [home],
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
      '/api/v1/alerts': [],
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
      '/api/v1/backups': [],
      '/api/v1/exports': [],
      '/api/v1/health/ready': { status: 'healthy', checks: { database: 'healthy' } },
      '/api/v1/admin/network/runtime': { ingress: 'signed_private', pull: 'disabled' },
      '/api/v1/audit-events': [],
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

test('legacy routes redirect without rendering a legacy page', async ({ page }) => {
  await page.goto('/devices')
  await expect(page).toHaveURL(/\/settings\/sensors/)
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Sensors' })).toBeVisible()
})

test('Billing and Settings remain usable at narrow widths', async ({ page }) => {
  await page.goto('/billing')
  await expect(page.getByRole('heading', { name: 'Billing' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Upload electric bill' })).toBeVisible()
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
    { legacy: '/status-indicators', canonical: '/settings/advanced' },
  ]
  for (const { legacy, canonical } of redirects) {
    await page.goto(legacy)
    await expect(page).toHaveURL(new RegExp(`${canonical.replace('/', '\\/')}($|\\?)`))
  }
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
        user: { id: 'owner-1', email: 'owner@example.test', display_name: 'Home Owner', roles: ['admin'], permissions: [], all_sites: true, site_ids: [] },
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
