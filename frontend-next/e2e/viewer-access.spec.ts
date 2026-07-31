import { expect, test } from '@playwright/test'

const VIEWER_PERMISSIONS = [
  'overview.view',
  'usage.view',
  'history.view',
  'costs.view',
  'sites.view',
  'utility_accounts.view',
  'topology.view',
  'devices.view',
  'rates.view',
  'alerts.view',
  'status_indicators.view',
]

test('Viewer receives the three read-only workspaces without private or mutating requests', async ({ page }, testInfo) => {
  const requests: Array<{ method: string; path: string }> = []
  await page.addInitScript(() => {
    localStorage.setItem('pm-single-home-onboarding-complete', 'true')
  })
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    requests.push({ method: request.method(), path })
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 })
    const responses: Record<string, unknown> = {
      '/api/v1/auth/session': {
        authenticated: true,
        bootstrap_required: false,
        user: {
          id: 'viewer-1',
          email: 'viewer@example.test',
          display_name: 'Greg',
          roles: ['viewer'],
          permissions: VIEWER_PERMISSIONS,
          all_sites: true,
          site_ids: [],
          access_revision: 4,
        },
      },
      '/api/v1/sites': [{
        id: 'home-1',
        name: 'Upland Home',
        timezone: 'America/Los_Angeles',
        currency: 'USD',
        lifecycle_state: 'active',
        is_default: true,
        revision: 1,
      }],
      '/api/v1/devices': [],
      '/api/v1/utility-accounts': [],
      '/api/v1/electric-services/default/current-rate-assignment': {
        schema_version: 'current-rate-assignment/1.0',
        home_id: 'home-1',
        electric_service_id: null,
        assignment: null,
      },
      '/api/v1/configuration-status': {
        schema_version: 'configuration-status/1.0',
        home_id: 'home-1',
        state: 'waiting_for_data',
        label: 'Waiting for data',
        summary: 'No live readings are available.',
        generated_at: '2026-07-31T16:00:00Z',
        issues: [],
      },
      '/api/v1/fleet/summary': {
        current_load_w: null,
        energy_today_kwh: null,
        estimated_cost_today: null,
        reporting_devices: 0,
        active_alerts: 0,
        recent_peak_w: null,
        latest_data_at: null,
        has_live_data: false,
        has_energy_data: false,
        has_cost_data: false,
      },
      '/api/v1/notifications': { items: [], page: 1, page_size: 200, total: 0 },
      '/api/v1/rates/plans': { plans: [] },
      '/api/v1/history/query': {
        source: { bucket: '1 hour' },
        scope: { display_name: 'Whole Home', timezone: 'America/Los_Angeles' },
        summary: {
          starts_at: '2026-07-25T07:00:00Z',
          ends_at: '2026-08-01T07:00:00Z',
          energy_kwh: null,
          energy_cost: null,
          coverage_percent: '0',
          contributing_sensor_count: 0,
        },
        combined: [],
        warnings: [],
      },
    }
    const response = responses[path]
    return response === undefined ? route.fulfill({ json: [] }) : route.fulfill({ json: response })
  })

  await page.goto('/home')
  await expect(page.getByRole('heading', { name: /Good (morning|afternoon|evening), Greg/ })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Primary' }).getByText('Settings')).toHaveCount(0)
  await expect(page.getByRole('link', { name: /Connect sensor|Manage sensors/ })).toHaveCount(0)
  const capturesViewer = ['desktop', 'mobile', 'edge', 'firefox', 'webkit'].includes(testInfo.project.name)
  if (capturesViewer) await expect(page).toHaveScreenshot('viewer-home.png', { fullPage: true, animations: 'disabled' })

  await page.getByRole('link', { name: 'History' }).click()
  await expect(page.getByRole('heading', { name: 'History' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Export' })).toHaveCount(0)
  if (capturesViewer) await expect(page).toHaveScreenshot('viewer-history.png', { fullPage: true, animations: 'disabled' })

  await page.getByRole('link', { name: 'Billing' }).click()
  await expect(page.getByRole('heading', { name: 'Billing' })).toBeVisible()
  await expect(page.getByRole('button', { name: /Upload|Import|Edit|Remove|Replace/ })).toHaveCount(0)
  if (capturesViewer) await expect(page).toHaveScreenshot('viewer-billing.png', { fullPage: true, animations: 'disabled' })

  await page.goto('/settings')
  await expect(page.getByText('Access denied', { exact: true })).toBeVisible()

  expect(requests.some(({ path }) => path === '/api/v1/admin/utility-bill-imports')).toBe(false)
  expect(requests.some(({ path }) => path.startsWith('/api/v1/backups'))).toBe(false)
  expect(requests.filter(({ method, path }) => method !== 'GET' && path !== '/api/v1/history/query')).toEqual([])
})
