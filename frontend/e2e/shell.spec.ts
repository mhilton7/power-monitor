import { expect, test, type Page } from '@playwright/test'

const session = (roles: string[] = ['admin']) => ({
  authenticated: true,
  bootstrap_required: false,
  user: { id: 'user-1', email: 'owner@example.test', display_name: 'Fleet Owner', roles },
})

const site = { id: 'site-1', name: 'Upland Site', timezone: 'America/Los_Angeles', allowed_cidrs: [], allowed_domains: [], allow_public_polling: false }
const fleet = { current_load_w: '960', energy_today_kwh: '12.5', estimated_cost_today: '4.25', billing_cycle_energy_kwh: '244', estimated_billing_cycle_cost: '83.11', online_devices: 1, synchronized_devices: 1, total_devices: 1, active_alerts: 1, current_tou_bucket: 'on-peak', recent_peak_w: '1800', disclosure: 'Estimate, not utility bill.' }
const device = { id: 'device-1', name: 'Garage HVAC', site_id: 'site-1', site_name: 'Upland Site', circuit_id: 'branch-1', circuit_name: 'Garage branch', connection_mode: 'hybrid', measurement_role: 'submeter', cost_scope: 'energy_only', included_in_default: true, ct_rating_amps: '100', status: 'online_synchronized', lifecycle_status: 'active', current_watts: '960', last_seen_at: '2026-07-20T06:00:00Z', firmware_version: '1.0.0', rssi_dbm: -52, pzem_ok: true, sd_ok: true, time_trusted: true, backlog: 0 }
const officialVersion = { id: 'rate-version-official', version: 1, effective_from: '2026-06-01', effective_through: null, status: 'active', source_kind: 'official_sce', source_checked_at: '2026-07-19T10:15:00Z', source_label: 'SCE archived evidence', integrity_sha256: 'a'.repeat(64), is_active: true, immutable: true, created_at: '2026-06-01T00:00:00Z' }
const officialPlan = { id: 'rate-plan-official', code: 'TOU-D-4-9PM', name: 'TOU-D 4 PM to 9 PM', description: 'Official SCE residential time-of-use plan.', plan_kind: 'official_sce', ownership_scope: 'global', currency: 'USD', timezone: 'America/Los_Angeles', status: 'active', versions: [officialVersion] }

async function mockApplication(page: Page, roles: string[] = ['admin']) {
  let enrollmentCounter = 0
  let sensorRemoved = false
  let customDocument: Record<string, unknown> | undefined
  let customVersionStatus = 'draft'
  let candidateStatus = 'pending_review'
  let jobPolls = 0
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    let body: unknown = []
    if (path === '/api/v1/auth/session') body = session(roles)
    else if (path === '/api/v1/sites') body = [site]
    else if (path === '/api/v1/fleet/summary') body = fleet
    else if (path === '/api/v1/devices') {
      const lifecycle = url.searchParams.get('lifecycle')
      if (lifecycle === 'decommissioned') body = sensorRemoved ? [{ ...device, status: 'decommissioned', lifecycle_status: 'decommissioned', circuit_id: undefined, circuit_name: undefined, decommissioned_at: '2026-07-20T07:00:00Z', decommissioned_by_name: 'Fleet Owner', decommission_reason: 'replaced', retained_history: true, re_enrollment_allowed: true }] : []
      else body = sensorRemoved && lifecycle === 'active' ? [] : [device]
    }
    else if (path === '/api/v1/devices/device-1') body = { device: { ...device, hardware_id: 'esp32-garage-001' }, history: { reading_count: 1440, earliest_reading_at: '2026-06-20T06:00:00Z', latest_reading_at: '2026-07-20T06:00:00Z', retained: true }, lifecycle_events: [] }
    else if (path === '/api/v1/admin/devices/device-1/unclaim' && request.method() === 'POST') { sensorRemoved = true; body = { device_id: 'device-1', status: 'decommissioned', already_decommissioned: false, historical_data_retained: true } }
    else if (path === '/api/v1/events/stream') {
      await route.fulfill({ contentType: 'text/event-stream', body: 'event: fleet\ndata: {"type":"fleet","devices":[]}\n\n' })
      return
    } else if (path === '/api/v1/circuits') body = [{ id: 'main-1', site_id: 'site-1', name: 'Main panel', measurement_role: 'main' }, { id: 'branch-1', site_id: 'site-1', parent_id: 'main-1', name: 'Garage branch', measurement_role: 'branch' }]
    else if (path === '/api/v1/aggregate-sets') body = [{ id: 'aggregate-1', name: 'Explicit home total', cost_scope: 'full_account', is_default: true, members: [{ circuit_id: 'main-1' }, { circuit_id: 'branch-1' }], overlap_confirmed_at: '2026-07-20T06:00:00Z' }]
    else if (path === '/api/v1/readings/history') body = { points: [{ timestamp: '2026-07-20T05:59:00Z', power_w: '900', quality_flags: [] }, { timestamp: '2026-07-20T06:00:00Z', power_w: '960', quality_flags: [] }], missing_ranges: [{ start_sequence: 2, end_sequence: 3 }], coverage_percent: '98.5' }
    else if (path === '/api/v1/alerts') body = [{ id: 'alert-1', name: 'Synchronization backlog', status: 'active', severity: 'warning', device_id: 'device-1', opened_at: '2026-07-20T06:00:00Z', evidence: { backlog: 42 } }]
    else if (path === '/api/v1/utility-accounts') body = [{ id: 'account-1', name: 'Home utility account' }]
    else if (path === '/api/v1/rates/plans' && request.method() === 'GET') body = [
      officialPlan,
      ...(customDocument ? [{ id: 'custom-plan-1', code: customDocument.plan_code, name: customDocument.plan_name, description: customDocument.description, plan_kind: 'custom', ownership_scope: customDocument.ownership_scope, currency: 'USD', timezone: 'America/Los_Angeles', status: customVersionStatus === 'active' ? 'active' : 'draft', versions: [{ ...officialVersion, id: 'custom-version-1', status: customVersionStatus, source_kind: 'custom', source_label: 'Administrator-defined rate plan', integrity_sha256: 'b'.repeat(64), is_active: customVersionStatus === 'active', immutable: customVersionStatus === 'active' }] }] : []),
    ]
    else if (path === '/api/v1/rates/plans' && request.method() === 'POST') {
      customDocument = JSON.parse(request.postData() ?? '{}') as Record<string, unknown>
      body = { plan: { id: 'custom-plan-1', versions: [{ ...officialVersion, id: 'custom-version-1', status: 'draft', is_active: false, immutable: false }] }, document: customDocument }
    }
    else if (path === '/api/v1/rates/versions/custom-version-1' && request.method() === 'GET') body = { version: { ...officialVersion, id: 'custom-version-1', status: customVersionStatus, is_active: customVersionStatus === 'active', immutable: customVersionStatus === 'active' }, document: customDocument }
    else if (path === '/api/v1/rates/versions/custom-version-1' && request.method() === 'PATCH') { customDocument = JSON.parse(request.postData() ?? '{}') as Record<string, unknown>; body = { version: { id: 'custom-version-1', status: 'draft' }, validation: { valid: true, errors: [], warnings: [], integrity_sha256: 'b'.repeat(64), coverage: { 'all-year/all-days': true } } } }
    else if (path === '/api/v1/rates/validate-document') body = { valid: true, errors: [], warnings: [], integrity_sha256: 'b'.repeat(64), coverage: { 'all-year/all-days': true } }
    else if (path === '/api/v1/rates/versions/custom-version-1/activate') { customVersionStatus = 'active'; body = { status: 'active', version: { id: 'custom-version-1', status: 'active' }, validation: { valid: true, errors: [], warnings: [], integrity_sha256: 'b'.repeat(64), coverage: { 'all-year/all-days': true } } } }
    else if (path === '/api/v1/rates/assignments' && request.method() === 'POST') body = { id: 'assignment-1', effective_from: '2026-07-20T00:00:00Z' }
    else if (path === '/api/v1/rates/preview-cost') body = { display_total: '0.25' }
    else if (path === '/api/v1/admin/rate-sources' && request.method() === 'GET') body = { configuration: { enabled: true, schedule_cron: '15 3 * * 0', timezone: 'America/Los_Angeles', jitter_minutes: 20, approval_mode: 'manual_review', auto_activate_verified: false, next_scheduled_run: '2026-07-26T10:15:00Z' }, last_successful_check: '2026-07-19T10:15:00Z', sources: [{ id: 'source-1', name: 'SCE public TOU page', url: 'https://www.sce.com/save-money/rates-financing/residential-rate-plans/time-of-use-plans', parser_id: 'sce_public_tou_html_v1', enabled: true, last_success_at: '2026-07-19T10:15:00Z', consecutive_failures: 0 }] }
    else if (path === '/api/v1/admin/rate-candidates' && request.method() === 'GET') body = [{ id: 'candidate-1', status: candidateStatus, risk_level: 'manual_review', summary: { plan_code: 'TOU-D-4-9PM', material_differences: 1 }, created_at: '2026-07-20T10:20:00Z' }]
    else if (path === '/api/v1/admin/rate-candidates/candidate-1' && request.method() === 'GET') body = { id: 'candidate-1', status: candidateStatus, risk_level: 'manual_review', summary: { plan_code: 'TOU-D-4-9PM', material_differences: 1 }, created_at: '2026-07-20T10:20:00Z', source_evidence: { artifact_id: 'artifact-1', sha256: 'c'.repeat(64), captured_at: '2026-07-20T10:19:00Z', parser_id: 'sce_public_tou_html_v1', parser_version: '1.0.0', warnings: [] }, differences: [{ path: 'seasons.0.schedules.0.periods.0.price_per_kwh', change_type: 'changed', before: '0.34', after: '0.35', material: true }] }
    else if (path === '/api/v1/admin/rate-candidates/candidate-1/approve') { candidateStatus = 'approved'; body = { status: 'approved' } }
    else if (path === '/api/v1/admin/rate-candidates/candidate-1/activate') { candidateStatus = 'activated'; body = { status: 'active' } }
    else if (path === '/api/v1/admin/rate-sources/check-now') { body = { job_id: 'rate-job-1', status: 'queued' } }
    else if (path === '/api/v1/jobs/rate-job-1') { jobPolls += 1; body = { id: 'rate-job-1', status: jobPolls > 1 ? 'succeeded' : 'running', progress: { completed: jobPolls > 1 ? 1 : 0, source_ids: ['source-1'] }, result: { candidate_count: 1 } } }
    else if (path === '/api/v1/admin/rate-checks') body = [{ id: 'check-1', rate_source_id: 'source-1', checked_at: '2026-07-20T10:20:00Z', outcome: 'succeeded', http_status: 200 }]
    else if (path === '/api/v1/alert-rules' && request.method() === 'GET') body = [
      { id: 'rule-disconnect', name: 'Sensor disconnected', rule_type: 'heartbeat_stale', severity: 'critical', enabled: true, debounce_seconds: 0, resolve_seconds: 30, configuration: { stale_seconds: 60 } },
      { id: 'rule-surge', name: 'Power surge', rule_type: 'power_surge', severity: 'critical', enabled: false, debounce_seconds: 10, resolve_seconds: 30, configuration: { threshold_watts: 5000 } },
    ]
    else if (path.startsWith('/api/v1/alert-rules/') && request.method() === 'PUT') body = { id: path.split('/').at(-1), enabled: true }
    else if (path === '/api/v1/notification-channels' && request.method() === 'GET') body = []
    else if (path === '/api/v1/notification-channels' && request.method() === 'POST') body = { id: 'smtp-1', name: 'Power Monitor email', channel_type: 'smtp', enabled: true, target: { host: 'smtp.example.com', port: 587, from: 'monitor@example.com', recipient_count: 1, starttls: true, implicit_tls: false, authentication_configured: true, event_types: ['heartbeat_stale', 'power_surge'] }, secrets_redacted: true }
    else if (path === '/api/v1/notification-attempts') body = []
    else if (path === '/api/v1/backups') body = []
    else if (path === '/api/v1/admin/logs/availability') body = { earliest_date: '2026-06-01', latest_date: '2026-07-20', retention_days: 90, stored_size_bytes: 15360, last_rotation_at: '2026-07-20T02:00:00Z', services: [{ id: 'api', available: true, stored_size_bytes: 8192 }, { id: 'worker', available: true, stored_size_bytes: 4096 }, { id: 'enrollment', available: true, stored_size_bytes: 1024 }, { id: 'device_sync', available: true, stored_size_bytes: 1024 }, { id: 'rate_sync', available: false, stored_size_bytes: 0 }, { id: 'backup', available: true, stored_size_bytes: 1024 }] }
    else if (path === '/api/v1/admin/logs/exports' && request.method() === 'POST') {
      await new Promise((resolve) => setTimeout(resolve, 120))
      body = { id: 'log-export-1', status: 'ready', start_date: '2026-07-14', end_date: '2026-07-20', services: ['api', 'worker', 'enrollment', 'device_sync', 'rate_sync', 'backup'], size_bytes: 9000, download_url: '/api/v1/admin/logs/exports/log-export-1/download' }
    }
    else if (path === '/api/v1/admin/logs/exports/log-export-1/download') {
      await route.fulfill({ contentType: 'application/zip', body: 'mock-zip-content' })
      return
    }
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

async function captureDashboardCorrection(page: Page, filename: string) {
  if (process.env.CAPTURE_DASHBOARD_SCREENSHOTS !== '1') return
  await page.screenshot({
    path: `../docs/screenshots/dashboard-corrections/${filename}`,
    fullPage: false,
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

test('cost workspace is removed from navigation and routing', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/')
  await expect(page.getByRole('link', { name: 'Costs' })).toHaveCount(0)
  await page.goto('/costs')
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole('heading', { name: 'Power Dashboard' })).toBeVisible()
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

test('administrator removes a claimed sensor with exact confirmation and can view it archived', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/enrollment')
  const claimedRow = page.getByRole('row').filter({ hasText: 'Garage HVAC' })
  await claimedRow.getByRole('button', { name: 'Remove sensor' }).click()
  const dialog = page.getByRole('dialog', { name: 'Remove sensor' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('device-1')).toBeVisible()
  await expect(dialog.getByText('Upland Site · Garage branch')).toBeVisible()
  await expect(dialog.getByText('1,440')).toBeVisible()
  await captureDashboardCorrection(page, 'sensor-removal-confirmation.png')
  const confirmation = dialog.getByLabel(/Type Garage HVAC or the immutable ID to confirm/)
  const removeButton = dialog.getByRole('button', { name: 'Remove sensor', exact: true })
  await expect(removeButton).toBeDisabled()
  await confirmation.fill('wrong value')
  await expect(dialog.getByText('The confirmation does not match')).toBeVisible()
  await confirmation.fill('Garage HVAC')
  await dialog.getByLabel(/Removal reason/).selectOption('replaced')
  const removalRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/admin/devices/device-1/unclaim') && request.method() === 'POST')
  await removeButton.click()
  const payload = JSON.parse((await removalRequest).postData() ?? '{}') as { confirmation: string; reason: string }
  expect(payload).toEqual({ confirmation: 'Garage HVAC', reason: 'replaced' })
  await expect(page.getByText('Sensor removed successfully.')).toBeVisible()
  await expect(page.getByText('No claimed sensors')).toBeVisible()
  await page.getByRole('tab', { name: /Archived sensors/ }).click()
  await expect(page.getByRole('row').filter({ hasText: 'Garage HVAC' })).toContainText('Preserved')
  await expect(page.getByText('Re-enrollment allowed')).toBeVisible()
  await captureDashboardCorrection(page, 'archived-sensors.png')
})

test('administrator can configure SMTP and notification timing', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/admin')
  await page.getByRole('tab', { name: 'Notifications' }).click()
  await page.getByRole('button', { name: 'Configure SMTP' }).click()
  await page.getByLabel('SMTP host').fill('smtp.example.com')
  await page.getByLabel('Username').fill('mailer')
  await page.getByLabel('Password').fill('smtp-secret-password')
  await page.getByLabel('From address').fill('monitor@example.com')
  await page.getByLabel('Recipients').fill('owner@example.com')
  const smtpRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/notification-channels') && request.method() === 'POST')
  await page.getByRole('button', { name: 'Save SMTP securely' }).click()
  const smtpPayload = JSON.parse((await smtpRequest).postData() ?? '{}') as { configuration: { event_types: string[] } }
  expect(smtpPayload.configuration.event_types).toEqual(expect.arrayContaining(['heartbeat_stale', 'power_surge', 'rate_candidate_pending', 'rate_source_conflict']))

  await page.getByLabel('Enable power surge notifications').check()
  await page.getByLabel('Power surge threshold').fill('7200')
  await page.getByLabel('Power surge duration').fill('15')
  const surgeRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/alert-rules/rule-surge') && request.method() === 'PUT')
  await page.getByRole('button', { name: 'Save notification triggers' }).click()
  const surgePayload = JSON.parse((await surgeRequest).postData() ?? '{}') as { enabled: boolean; debounce_seconds: number; configuration: { threshold_watts: number } }
  expect(surgePayload).toMatchObject({ enabled: true, debounce_seconds: 15, configuration: { threshold_watts: 7200 } })
})

test('rate manager creates, validates, activates, and assigns a custom TOU plan', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/rates')
  await expect(page.getByRole('heading', { name: 'Rate plans' })).toBeVisible()
  await page.getByRole('button', { name: /Custom plan/ }).click()
  await page.getByLabel('Plan name').fill('Weekday and weekend test plan')
  await page.getByLabel('Plan code').fill('CUSTOM-E2E')
  await page.getByRole('button', { name: /Next/ }).click()
  await expect(page.getByRole('button', { name: /Seasons & schedules/ })).toHaveAttribute('aria-current', 'step')
  await expect(page.getByLabel('Total price per kWh')).toHaveValue('0.25000000')
  await page.getByLabel('End minute').fill('1380')
  await expect(page.getByText('This schedule does not cover the full day')).toBeVisible()
  await page.getByLabel('End minute').fill('1440')
  await expect(page.getByText('This schedule does not cover the full day')).toHaveCount(0)
  const chargesStep = page.getByRole('button', { name: /Charges & adjustments/ })
  await chargesStep.focus()
  await page.keyboard.press('Enter')
  await expect(chargesStep).toHaveAttribute('aria-current', 'step')
  await expect(page.getByText('Whole-account items are ignored')).toBeVisible()
  await page.getByRole('button', { name: /Next/ }).click()
  await page.getByRole('button', { name: /Save draft/ }).click()
  await expect(page).toHaveURL(/\/rates\/custom-plan-1\/versions\/custom-version-1/)
  await page.getByRole('button', { name: 'Validate', exact: true }).click()
  await expect(page.getByText('Ready to activate')).toBeVisible()
  await page.getByRole('button', { name: 'Activate', exact: true }).click()
  const activationDialog = page.getByRole('dialog', { name: 'Activate rate version' })
  await expect(activationDialog).toBeVisible()
  await activationDialog.getByRole('button', { name: 'Activate version' }).click()
  await expect(page.getByLabel('Utility account')).toBeEnabled()
  await page.getByLabel('Utility account').selectOption('account-1')
  await expect(page.getByText('Rate version assigned.')).toBeVisible()
})

test('administrator monitors an SCE job and reviews candidate evidence', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/rates/sources')
  await page.getByRole('button', { name: /Check SCE now/ }).click()
  await expect(page.getByText('SCE check succeeded')).toBeVisible({ timeout: 8_000 })
  await page.getByRole('button', { name: /TOU-D-4-9PM/ }).click()
  await expect(page.getByText('Archived evidence')).toBeVisible()
  await expect(page.getByText(/price_per_kwh/)).toBeVisible()
  await page.getByRole('button', { name: 'Approve' }).click()
  await expect(page.getByRole('button', { name: /Activate approved version/ })).toBeVisible()
  await page.getByRole('button', { name: /Activate approved version/ }).click()
  await expect(page.getByText('activated').last()).toBeVisible()
})

test('administrator downloads a seven-day application-log export from Backups', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/admin')
  await page.getByRole('tab', { name: 'Backups' }).click()
  await expect(page.getByRole('heading', { name: 'Application logs' })).toBeVisible()
  await expect(page.getByText('90 days', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Start date')).toHaveValue('2026-07-14')
  await expect(page.getByLabel('End date')).toHaveValue('2026-07-20')
  await expect(page.getByLabel('Service or category')).toHaveValue('all')
  await captureDashboardCorrection(page, 'application-logs.png')
  const exportRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/admin/logs/exports') && request.method() === 'POST')
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download logs' }).click()
  await expect(page.getByText('Preparing and securely redacting the export…')).toBeVisible()
  const exportPayload = JSON.parse((await exportRequest).postData() ?? '{}') as { services: string[] }
  expect(exportPayload.services).toHaveLength(6)
  await download
  await expect(page.getByText('Log export is ready.')).toBeVisible()
})

test('dashboard copy is corrected without exposing protocol or footer status text', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/')
  await expect(page.getByText('Fleet availability')).toHaveCount(0)
  await expect(page.getByText('100%', { exact: true })).toBeVisible()
  await page.goto('/devices')
  await expect(page.getByRole('heading', { name: 'Device Management' })).toBeVisible()
  await expect(page.getByText('Sensor health and general data')).toBeVisible()
  await captureDashboardCorrection(page, 'device-management.png')
  await expect(page.getByText(/pm-protocol\/1\.0\.0/i)).toHaveCount(0)
  await expect(page.getByText(/server protected/i)).toHaveCount(0)
  await expect(page.getByText(/multi-sensor fleet/i)).toHaveCount(0)
  await expect(page.getByText(/signed heartbeats.*local custody/i)).toHaveCount(0)
  await page.goto('/alerts')
  await expect(page.getByRole('heading', { name: 'Alerts & Notifications' })).toBeVisible()
  await expect(page).toHaveTitle('Alerts & Notifications · Power Monitor')
  await expect(page.getByText(/Evidence, debounce, resolution, acknowledgement/)).toHaveCount(0)
  await captureDashboardCorrection(page, 'alerts-and-notifications.png')
})

test('search and dropdown controls keep compact pointer focus and visible keyboard focus', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/devices')
  const search = page.getByPlaceholder('Search devices')
  const initialBox = await search.boundingBox()
  await search.click()
  expect(await search.evaluate((element) => getComputedStyle(element).outlineWidth)).toBe('0px')
  expect(await search.evaluate((element) => getComputedStyle(element).boxShadow)).toBe('none')
  expect(await search.evaluate((element) => element.parentElement ? getComputedStyle(element.parentElement).boxShadow : '')).not.toContain('3px')
  expect(await search.boundingBox()).toEqual(initialBox)

  await page.locator('body').click({ position: { x: 2, y: 2 } })
  for (let index = 0; index < 30; index += 1) {
    await page.keyboard.press('Tab')
    if (await search.evaluate((element) => element === document.activeElement)) break
  }
  expect(await search.evaluate((element) => element.matches(':focus-visible'))).toBe(true)
  expect(await search.evaluate((element) => element.parentElement ? getComputedStyle(element.parentElement).outlineWidth : '')).toBe('2px')
  await page.keyboard.type('Garage')
  await expect(search).toHaveValue('Garage')

  const status = page.getByLabel('Status')
  await status.focus()
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Enter')
  await expect(status).not.toHaveValue('all')
  expect(await status.evaluate((element) => element.parentElement ? getComputedStyle(element.parentElement).outlineWidth : '')).toBe('2px')

  await page.locator('body').click({ position: { x: 2, y: 2 } })
  const restingStyles = await status.evaluate((element) => {
    const styles = getComputedStyle(element.parentElement as HTMLElement)
    return { borderColor: styles.borderColor, boxShadow: styles.boxShadow, outlineStyle: styles.outlineStyle }
  })
  await status.click()
  await status.selectOption('offline_last_known')
  await expect(status).not.toBeFocused()
  expect(await status.evaluate((element) => {
    const styles = getComputedStyle(element.parentElement as HTMLElement)
    return { borderColor: styles.borderColor, boxShadow: styles.boxShadow, outlineStyle: styles.outlineStyle }
  })).toEqual(restingStyles)
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
