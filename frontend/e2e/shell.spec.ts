import { expect, test, type Page } from '@playwright/test'

const session = (roles: string[] = ['admin']) => ({
  authenticated: true,
  bootstrap_required: false,
  user: { id: 'user-1', email: 'owner@example.test', display_name: 'Fleet Owner', roles },
})

const site = { id: 'site-1', name: 'Upland Site', timezone: 'America/Los_Angeles', allowed_cidrs: [], allowed_domains: [], allow_public_polling: false }
const fleet = { current_load_w: '960', energy_today_kwh: '12.5', estimated_cost_today: '4.25', billing_cycle_energy_kwh: '244', estimated_billing_cycle_cost: '83.11', online_devices: 1, synchronized_devices: 1, total_devices: 1, active_alerts: 1, current_tou_bucket: 'on-peak', recent_peak_w: '1800', disclosure: 'Estimate, not utility bill.' }
const device = { id: 'device-1', name: 'Garage HVAC', site_id: 'site-1', circuit_id: 'branch-1', connection_mode: 'hybrid', measurement_role: 'submeter', cost_scope: 'energy_only', included_in_default: true, ct_rating_amps: '100', status: 'online_synchronized', current_watts: '960', last_seen_at: '2026-07-20T06:00:00Z', firmware_version: '1.0.0', rssi_dbm: -52, pzem_ok: true, sd_ok: true, time_trusted: true, backlog: 0 }

async function mockApplication(page: Page, roles: string[] = ['admin']) {
  let enrollmentCounter = 0
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    let body: unknown = []
    if (path === '/api/v1/auth/session') body = session(roles)
    else if (path === '/api/v1/sites') body = [site]
    else if (path === '/api/v1/fleet/summary') body = fleet
    else if (path === '/api/v1/devices') body = [device]
    else if (path === '/api/v1/events/stream') {
      await route.fulfill({ contentType: 'text/event-stream', body: 'event: fleet\ndata: {"type":"fleet","devices":[]}\n\n' })
      return
    } else if (path === '/api/v1/circuits') body = [{ id: 'main-1', site_id: 'site-1', name: 'Main panel', measurement_role: 'main' }, { id: 'branch-1', site_id: 'site-1', parent_id: 'main-1', name: 'Garage branch', measurement_role: 'branch' }]
    else if (path === '/api/v1/aggregate-sets') body = [{ id: 'aggregate-1', name: 'Explicit home total', cost_scope: 'full_account', is_default: true, members: [{ circuit_id: 'main-1' }, { circuit_id: 'branch-1' }], overlap_confirmed_at: '2026-07-20T06:00:00Z' }]
    else if (path === '/api/v1/readings/history') body = { points: [{ timestamp: '2026-07-20T05:59:00Z', power_w: '900', quality_flags: [] }, { timestamp: '2026-07-20T06:00:00Z', power_w: '960', quality_flags: [] }], missing_ranges: [{ start_sequence: 2, end_sequence: 3 }], coverage_percent: '98.5' }
    else if (path === '/api/v1/alerts') body = [{ id: 'alert-1', name: 'Synchronization backlog', status: 'active', severity: 'warning', device_id: 'device-1', opened_at: '2026-07-20T06:00:00Z', evidence: { backlog: 42 } }]
    else if (path === '/api/v1/alert-rules') body = [{ id: 'rule-1', name: 'Backlog', rule_type: 'sync_backlog', severity: 'warning', enabled: true, debounce_seconds: 60, resolve_seconds: 60 }]
    else if (path === '/api/v1/users') body = [
      { ...session(roles).user, is_active: true },
      { id: 'user-2', email: 'viewer@example.test', display_name: 'Dashboard Viewer', roles: ['viewer'], is_active: true },
    ]
    else if (path === '/api/v1/enrollment-tokens' && request.method() === 'POST') {
      enrollmentCounter += 1
      const payload = JSON.parse(request.postData() ?? '{}') as { name?: string }
      body = { id: `token-${enrollmentCounter}`, token: enrollmentCounter === 1 ? 'public-one-time-enrollment-token' : `public-one-time-enrollment-token-${enrollmentCounter}`, expires_at: new Date(Date.now() + 600_000).toISOString(), preassignment: { name: payload.name } }
    }
    else if (path === '/api/v1/alerts/alert-1/acknowledge') body = { acknowledged: true }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
  })
}

test('unauthenticated users see a secure sign-in surface', async ({ page }) => {
  await page.route('**/api/v1/auth/session', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ authenticated: false, bootstrap_required: false }) }))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Sign in to your dashboard' })).toBeVisible()
  await expect(page.getByText('Private fleet intelligence')).toBeVisible()
})

test('first run creates an administrator without a default password', async ({ page }) => {
  await page.route('**/api/v1/auth/session', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ authenticated: false, bootstrap_required: true }) }))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Create the administrator' })).toBeVisible()
  await expect(page.getByLabel('One-time bootstrap secret')).toBeVisible()
  await expect(page.getByText('There is no default password.')).toBeVisible()
})

test('viewer sees fleet evidence but cannot open operator or admin pages', async ({ page }) => {
  await mockApplication(page, ['viewer'])
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Power Dashboard' })).toBeVisible()
  await expect(page.getByText('960 W', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('link', { name: /Enroll sensor/ })).toHaveCount(0)
  await page.goto('/enrollment')
  await expect(page).toHaveURL(/\/$/)
  await page.goto('/admin')
  await expect(page).toHaveURL(/\/$/)
})

test('history keeps missing-data regions visible', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/history')
  await expect(page.getByRole('heading', { name: 'History & comparison' })).toBeVisible()
  await expect(page.getByText('Missing sequence regions')).toBeVisible()
  await expect(page.getByText('2–3')).toBeVisible()
  await expect(page.getByText('98.5%')).toBeVisible()
})

test('topology shows an explicitly confirmed overlap warning', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/topology')
  await expect(page.getByRole('heading', { name: 'Site & circuit topology' })).toBeVisible()
  await expect(page.getByText('Potential overlap was explicitly confirmed.')).toBeVisible()
  await expect(page.getByText('Never summed by default')).toBeVisible()
})

test('cost pages always disclose that estimates are not utility bills', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/costs')
  await expect(page.getByRole('heading', { name: 'Costs & billing' })).toBeVisible()
  await expect(page.getByText('Estimate, not utility bill.')).toBeVisible()
  await page.getByLabel('Cost scope').selectOption('full_account')
  await expect(page.getByText('Full-account mode is explicit.')).toBeVisible()
})

test('admin can create an enrollment token without seeing a permanent secret', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/enrollment')
  await page.getByLabel('Friendly name').fill('Garage HVAC')
  await page.getByRole('button', { name: /Add enrollment token/ }).click()
  await expect(page.getByText('1 token ready')).toBeVisible()
  await expect(page.getByText('public-one-time-enrollment-token')).toBeVisible()
  await page.getByLabel('Friendly name').fill('Water Heater')
  await page.getByRole('button', { name: /Add enrollment token/ }).click()
  await expect(page.getByText('2 tokens ready')).toBeVisible()
  await expect(page.getByText('Water Heater')).toBeVisible()
  await expect(page.getByText('Permanent device secrets are never shown here.')).toBeVisible()
})

test('administrator can create and remove local users', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/admin')
  await page.getByRole('button', { name: /Add user/ }).click()
  await page.getByLabel('Display name').fill('Energy Analyst')
  await page.getByLabel('Email address').fill('analyst@example.test')
  await page.getByLabel('Temporary password').fill('Production-Password-42!')
  const createRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/users') && request.method() === 'POST')
  await page.getByRole('button', { name: /Create user/ }).click()
  await createRequest

  const viewerRow = page.getByRole('row').filter({ hasText: 'Dashboard Viewer' })
  const removeRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/users/user-2') && request.method() === 'DELETE')
  await viewerRow.getByRole('button', { name: 'Remove' }).click()
  await viewerRow.getByRole('button', { name: 'Remove', exact: true }).click()
  await removeRequest
})

test('alert acknowledgement calls the audited server action', async ({ page }) => {
  await mockApplication(page)
  const acknowledgement = page.waitForRequest((request) => request.url().endsWith('/api/v1/alerts/alert-1/acknowledge') && request.method() === 'POST')
  await page.goto('/alerts')
  await page.getByRole('button', { name: 'Acknowledge' }).click()
  await acknowledgement
})

test('mobile navigation opens with keyboard-operable controls', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 760 })
  await mockApplication(page)
  await page.goto('/')
  const menu = page.getByRole('button', { name: 'Open navigation' })
  await menu.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Devices' })).toBeVisible()
})
