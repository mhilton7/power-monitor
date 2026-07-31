import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { PERMISSION_CODES } from '../src/access/permissions'

const home = {
  id: 'home-1', name: 'Upland Home', timezone: 'America/Los_Angeles', currency: 'USD',
  lifecycle_state: 'active', is_default: true, revision: 1,
}

function notification(code: string, title: string, resource: string, overrides: Record<string, unknown> = {}) {
  return {
    id: `notification-${code}`,
    code,
    kind: 'operational_alert',
    category: 'sensor_health',
    severity: 'error',
    state: 'open',
    title,
    summary: `${resource} reported a specific condition that requires review.`,
    affected_resource: { type: 'sensor', id: `sensor-${code}`, name: resource },
    first_seen_at: '2026-07-31T16:00:00Z',
    last_seen_at: '2026-07-31T16:01:32Z',
    occurrence_count: 3,
    duration_seconds: 92,
    observed: { label: 'Observed value', value: '92', unit: 'seconds' },
    expected: { label: 'Expected value', operator: 'within', value: '60', unit: 'seconds' },
    cause: { code, explanation: 'The authoritative health threshold was exceeded.' },
    evidence: [{ label: 'Last known power', value: '842.6 W', status: 'warning' }],
    impact: 'Monitoring and historical coverage may be delayed until the condition recovers.',
    remediation: {
      summary: 'Review the affected sensor and correct the reported condition.',
      steps: ['Confirm the sensor has power.', 'Open sensor details and review diagnostics.'],
      automatic_recovery: 'Power Monitor continues retrying bounded synchronization automatically.',
      action: { label: 'Open sensor details', target: '/settings/sensors', required_permissions: ['devices.view'] },
    },
    suppression: { dismissible: false, permanently_suppressible: false, currently_suppressed: false, allowed_scopes: [] },
    ...overrides,
  }
}

const notifications = [
  notification('server_failure', 'Power Monitor server needs attention', 'Power Monitor server', {
    severity: 'critical', category: 'server', affected_resource: { type: 'server', id: 'api', name: 'Power Monitor API' },
  }),
  notification('heartbeat_stale', 'Indoor-AC stopped reporting', 'Indoor-AC'),
  notification('pzem_failure', 'Outdoor-AC cannot read the energy meter', 'Outdoor-AC', {
    observed: { label: 'Consecutive failed reads', value: '12', unit: 'requests' },
    expected: { label: 'Meter reads', operator: 'equal to', value: '0', unit: 'failures' },
  }),
  notification('backup_failure', 'Backup verification failed', 'Jul 31 backup', {
    category: 'backup', affected_resource: { type: 'backup', id: 'backup-1', name: 'Jul 31 backup' },
  }),
  notification('sync_backlog', 'Outdoor-AC has 1,214 readings waiting to synchronize', 'Outdoor-AC', {
    severity: 'warning', observed: { label: 'Pending readings', value: '1,214', unit: 'readings' },
    expected: { label: 'Pending readings', operator: 'equal to', value: '0', unit: 'readings' },
  }),
  notification('recommendation.smtp_not_configured', 'Email notifications are not configured', 'Upland Home', {
    id: 'recommendation:recommendation.smtp_not_configured:home-1', kind: 'setup_recommendation', category: 'delivery', severity: 'info',
    summary: 'Dashboard alerts continue normally, but Power Monitor cannot send email.',
    affected_resource: { type: 'home', id: 'home-1', name: 'Upland Home' },
    observed: undefined, expected: undefined, evidence: [], impact: 'Only optional external email delivery is unavailable.',
    remediation: { summary: 'Set up email if you want external delivery.', steps: ['Open Notification settings.'], action: { label: 'Set up email', target: '/settings/notifications', required_permissions: ['alerts.manage_delivery'] } },
    suppression: { dismissible: true, permanently_suppressible: true, suppression_key: 'recommendation.smtp_not_configured', currently_suppressed: false, allowed_scopes: ['user', 'home'] },
  }),
  notification('notification_delivery_failed', 'Email delivery failed', 'Home email', {
    id: 'delivery-attempt-1', kind: 'delivery_issue', category: 'delivery', severity: 'warning',
    affected_resource: { type: 'notification_channel', id: 'channel-1', name: 'Home email' },
    summary: 'STARTTLS negotiation failed while sending Indoor-AC stopped reporting.',
    delivery: { attempted: true, channel_name: 'Home email', last_attempt_at: '2026-07-31T16:02:00Z', last_outcome: 'retry_scheduled', retry_at: '2026-07-31T16:07:00Z', safe_error_code: 'smtp_starttls_failed', safe_error_summary: 'STARTTLS negotiation failed' },
  }),
]

async function mockServer(page: Page, permissions = [...PERMISSION_CODES]) {
  const visibleNotifications = permissions.includes('alerts.manage_delivery')
    ? notifications
    : notifications.filter((item) => item.kind !== 'setup_recommendation')
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 })
    if (request.method() !== 'GET') return route.fulfill({ status: 200, json: { active: true, revision: 1 } })
    const data: Record<string, unknown> = {
      '/api/v1/auth/session': { authenticated: true, bootstrap_required: false, user: { id: 'owner-1', email: 'owner@example.test', display_name: 'Home Owner', roles: ['admin'], permissions, all_sites: true, site_ids: [], access_revision: 1 } },
      '/api/v1/sites': [home],
      '/api/v1/devices': [],
      '/api/v1/utility-accounts': [],
      '/api/v1/electric-services/default/current-rate-assignment': { schema_version: 'current-rate-assignment/1.0', home_id: home.id, electric_service_id: null, assignment: null },
      '/api/v1/configuration-status': { schema_version: 'configuration-status/1.0', home_id: home.id, state: 'waiting_for_data', label: 'Waiting for data', summary: 'No readings yet.', generated_at: '2026-07-31T16:00:00Z', issues: [] },
      '/api/v1/fleet/summary': { current_load_w: null, energy_today_kwh: null, estimated_cost_today: null, reporting_devices: 0, active_alerts: 5, recent_peak_w: null, latest_data_at: null, has_live_data: false, has_energy_data: false, has_cost_data: false },
      '/api/v1/notifications': { items: visibleNotifications, page: 1, page_size: 200, total: visibleNotifications.length },
      '/api/v1/alert-rules': [],
      '/api/v1/notification-channels': [{ id: 'channel-1', name: 'Home email', channel_type: 'smtp', enabled: true, target: { host: 'smtp.example.test', port: 587, from: 'alerts@example.test', recipient_count: 1, starttls: true }, secrets_redacted: true }],
      '/api/v1/notification-attempts': [{ id: 'attempt-1', channel_id: 'channel-1', status: 'retry_scheduled', safe_error_code: 'smtp_starttls_failed', safe_error_summary: 'STARTTLS negotiation failed', next_attempt_at: '2026-07-31T16:07:00Z' }],
      '/api/v1/notification-suppressions': [{ id: 'suppression-1', suppression_key: 'recommendation.smtp_not_configured', category: 'delivery', scope_type: 'home', scope_name: 'Upland Home', created_by: 'Home Owner', created_at: '2026-07-31T16:03:00Z', reason: 'Dashboard alerts are sufficient', source_notification_id: 'recommendation:recommendation.smtp_not_configured:home-1', active: true, revision: 1 }],
      '/api/v1/notification-history': { total: 2, items: [{ id: 'event-1', notification_id: 'notification-heartbeat_stale', event_type: 'opened', occurred_at: '2026-07-31T16:00:00Z', category: 'sensor_health', severity: 'error' }, { id: 'event-2', notification_id: 'delivery-attempt-1', event_type: 'delivery_failed', occurred_at: '2026-07-31T16:02:00Z', category: 'delivery', severity: 'warning' }] },
    }
    return route.fulfill({ json: data[path] ?? [] })
  })
}

async function capture(page: Page, testInfo: TestInfo, name: string) {
  await page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage: true, animations: 'disabled' })
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => { localStorage.setItem('pm-single-home-onboarding-complete', 'true') })
})

test('notification center explains critical, sensor, meter, backup, sync, recommendation, and delivery states', async ({ page }, testInfo) => {
  await mockServer(page)
  await page.goto('/home')
  await page.getByRole('button', { name: /active operational alerts/ }).click()
  await expect(page.getByRole('heading', { name: 'Active issues' })).toBeVisible()
  for (const title of notifications.map((item) => item.title)) await expect(page.getByText(title, { exact: true })).toBeVisible()
  const notificationButton = (title: string) => page.locator('button.notification-summary').filter({ has: page.getByText(title, { exact: true }) })
  await notificationButton('Indoor-AC stopped reporting').click()
  await expect(page.getByRole('heading', { name: 'What happened' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Why it matters' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'How to fix it' })).toBeVisible()
  await capture(page, testInfo, 'detailed-heartbeat-alert')
  await notificationButton('Indoor-AC stopped reporting').click()
  const detailCaptures: Array<[string, string]> = [
    ['Power Monitor server needs attention', 'critical-detailed-alert'],
    ['Outdoor-AC cannot read the energy meter', 'pzem-failure'],
    ['Backup verification failed', 'backup-failure'],
    ['Outdoor-AC has 1,214 readings waiting to synchronize', 'sync-backlog'],
    ['Email delivery failed', 'smtp-delivery-failure'],
  ]
  for (const [title, filename] of detailCaptures) {
    await notificationButton(title).click()
    await capture(page, testInfo, filename)
    await notificationButton(title).click()
  }
  await notificationButton('Email notifications are not configured').click()
  await capture(page, testInfo, 'smtp-setup-recommendation')
  await page.getByRole('button', { name: 'Do not remind me again' }).click()
  const dialog = page.getByRole('dialog', { name: 'Stop email setup reminders?' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Do not remind me again' })).toBeDisabled()
  await capture(page, testInfo, 'permanent-ignore-confirmation')
})

test('notification settings expose ignored reminders, delivery evidence, and restoration', async ({ page }, testInfo) => {
  await mockServer(page)
  await page.goto('/settings/notifications')
  await expect(page.getByRole('heading', { name: 'Alert rules' })).toBeVisible()
  await expect(page.getByText('STARTTLS negotiation failed')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Disable' })).toBeVisible()
  await expect(page.getByText('Email notifications are not configured')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Restore reminder' })).toBeVisible()
  await capture(page, testInfo, 'ignored-recommendations-and-delivery')
})

test('viewer sees operational detail without delivery-management actions', async ({ page }, testInfo) => {
  await mockServer(page, ['overview.view', 'sites.view', 'alerts.view'])
  await page.goto('/home')
  await page.getByRole('button', { name: /active operational alerts/ }).click()
  await expect(page.getByText('Email notifications are not configured')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Acknowledge' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Do not remind me again' })).toHaveCount(0)
  await capture(page, testInfo, 'viewer-notification-drawer')
})
