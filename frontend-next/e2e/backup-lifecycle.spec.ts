import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'

const home = {
  id: 'home-1',
  name: 'Upland Home',
  timezone: 'America/Los_Angeles',
  currency: 'USD',
  lifecycle_state: 'active',
  is_default: true,
  revision: 1,
}

const backupRows = [
  {
    id: '11111111-1111-4111-8111-111111111111',
    started_at: '2026-07-29T10:00:00Z',
    completed_at: '2026-07-29T10:01:00Z',
    status: 'verification_failed',
    size_bytes: 352_067,
    encrypted: false,
    manifest_hash: 'sha256:failed-sample',
    verification_attempt_count: 1,
    failed_stage: 'restore',
    safe_error_code: 'RESTORE_FAILED',
    safe_error_summary: 'The isolated restore test failed. Production data was not changed.',
    verification_details: {},
  },
  {
    id: '22222222-2222-4222-8222-222222222222',
    started_at: '2026-07-28T10:00:00Z',
    completed_at: '2026-07-28T10:01:00Z',
    verified_at: '2026-07-28T10:02:00Z',
    status: 'verified',
    size_bytes: 357_458,
    encrypted: false,
    manifest_hash: 'sha256:verified-newer',
    verification_attempt_count: 1,
    verification_details: { migration_revision: '20260730_0019', table_count: 98 },
  },
  {
    id: '33333333-3333-4333-8333-333333333333',
    started_at: '2026-07-27T10:00:00Z',
    completed_at: '2026-07-27T10:01:00Z',
    verified_at: '2026-07-27T10:02:00Z',
    status: 'verified',
    size_bytes: 348_211,
    encrypted: true,
    manifest_hash: 'sha256:verified-older',
    verification_attempt_count: 2,
    verification_details: { migration_revision: '20260730_0019', table_count: 98 },
  },
]

async function mockSettings(page: Page, writes: Array<{ method: string; path: string; body: unknown }>) {
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname
    if (request.method() !== 'GET') {
      writes.push({
        method: request.method(),
        path: pathname,
        body: request.postDataJSON(),
      })
      if (pathname === '/api/v1/backup-requests') {
        return route.fulfill({
          status: 202,
          json: {
            id: 'job-1',
            operation: 'create',
            status: 'queued',
            maintenance_required: false,
          },
        })
      }
      if (pathname === '/api/v1/backups/replace-all') {
        return route.fulfill({
          status: 202,
          json: {
            id: 'replace-job-1',
            operation: 'replace_all',
            status: 'queued',
            backup_id: '44444444-4444-4444-8444-444444444444',
            maintenance_required: false,
            progress: { stage: 'preparing', message: 'Preparing replacement backup' },
          },
        })
      }
      if (pathname.endsWith('/verify')) {
        return route.fulfill({ status: 202, json: backupRows[0] })
      }
      if (request.method() === 'DELETE') {
        return route.fulfill({
          status: 202,
          json: { ...backupRows[0], status: 'deleting' },
        })
      }
      return route.fulfill({ json: {} })
    }

    const data: Record<string, unknown> = {
      '/api/v1/auth/session': {
        authenticated: true,
        bootstrap_required: false,
        user: {
          id: 'owner-1',
          email: 'owner@example.test',
          display_name: 'Home Owner',
          roles: ['admin'],
          permissions: [],
          all_sites: true,
          site_ids: [],
        },
      },
      '/api/v1/sites': [home],
      '/api/v1/devices': [],
      '/api/v1/utility-accounts': [],
      '/api/v1/configuration-status': {
        schema_version: 'configuration-status/1.0',
        home_id: home.id,
        state: 'waiting_for_data',
        label: 'Waiting for data',
        summary: 'No live readings are available.',
        generated_at: '2026-07-30T12:00:00Z',
        issues: [],
      },
      '/api/v1/electric-services/default/current-rate-assignment': {
        schema_version: 'current-rate-assignment/1.0',
        home_id: home.id,
        electric_service_id: null,
        assignment: null,
      },
      '/api/v1/fleet/summary': {
        current_load_w: null,
        energy_today_kwh: null,
        estimated_cost_today: null,
        reporting_devices: 0,
        active_alerts: 0,
        recent_peak_w: null,
        has_live_data: false,
        has_energy_data: false,
        has_cost_data: false,
      },
      '/api/v1/alerts': [],
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
      '/api/v1/backups': backupRows,
      '/api/v1/backups/replace-all-preview': {
        existing_backup_count: 3,
        existing_storage_bytes: 1_057_736,
        incomplete_backup_count: 0,
        unverified_backup_count: 1,
        verified_backup_count: 2,
        estimated_reclaim_bytes: 1_057_736,
      },
      '/api/v1/backup-requests': [],
      '/api/v1/exports': [],
    }
    if (pathname === '/api/v1/events/stream') return route.fulfill({ status: 204 })
    return route.fulfill({ json: data[pathname] ?? [] })
  })
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('pm-single-home-onboarding-complete', 'true')
    localStorage.setItem('pm-theme', 'dark')
  })
})

test('Data & Backups renders truthful lifecycle states and does not mutate on load', async ({ page }, testInfo) => {
  const writes: Array<{ method: string; path: string; body: unknown }> = []
  await mockSettings(page, writes)
  await page.goto('/settings/data')

  await expect(page.getByRole('heading', { name: 'Data & Backups' })).toBeVisible()
  await expect(page.getByText('Verification failed', { exact: true })).toBeVisible()
  await expect(page.getByText('Verified', { exact: true })).toHaveCount(2)
  await expect(page.getByText('completed_unverified', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Retry verification' })).toBeVisible()
  await page.waitForTimeout(1_200)
  expect(writes).toEqual([])

  await page.getByRole('button', { name: 'Details' }).first().click()
  await expect(page.getByRole('dialog', { name: 'Backup details' })).toContainText('RESTORE_FAILED')
  await page.getByRole('button', { name: 'Close backup details' }).click()

  if (process.env.UPDATE_BACKUP_LIVE_DOCS === '1' && testInfo.project.name === 'desktop') {
    await page.screenshot({
      path: path.resolve('..', 'docs', 'screenshots', 'data-backups-repaired.png'),
      fullPage: true,
    })
  }
})

test('backup create is guarded from duplicate clicks and verify/delete require explicit actions', async ({ page }) => {
  const writes: Array<{ method: string; path: string; body: unknown }> = []
  await mockSettings(page, writes)
  await page.goto('/settings/data')

  await page.getByRole('button', { name: 'Back up now' }).dblclick()
  await expect.poll(() => writes.filter((item) => item.path === '/api/v1/backup-requests').length).toBe(1)

  await page.reload()
  await page.getByRole('button', { name: 'Retry verification' }).click()
  await expect.poll(() => writes.filter((item) => item.path.endsWith('/verify')).length).toBe(1)

  await page.reload()
  const answers = ['DELETE', '11111111', 'Remove failed test artifact']
  page.on('dialog', async (dialog) => { await dialog.accept(answers.shift()) })
  await page.getByRole('button', { name: 'Delete' }).first().click()
  await expect.poll(() => writes.filter((item) => item.method === 'DELETE').length).toBe(1)
  expect(writes.find((item) => item.method === 'DELETE')?.body).toEqual({
    confirmation: 'DELETE',
    backup_id_confirmation: '11111111',
    reason: 'Remove failed test artifact',
  })
})

test('replace-all shows inventory and requires the exact destructive confirmation', async ({ page }) => {
  const writes: Array<{ method: string; path: string; body: unknown }> = []
  await mockSettings(page, writes)
  await page.goto('/settings/data')

  await page.getByRole('button', { name: 'Replace all backups', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Replace all backups' })
  await expect(dialog).toContainText('Existing backup count')
  await expect(dialog).toContainText('3')
  await expect(dialog).toContainText('1 MB')
  const execute = dialog.getByRole('button', { name: 'Replace all backups', exact: true })
  await expect(execute).toBeDisabled()
  await dialog.getByLabel(/Type REPLACE ALL BACKUPS/).fill('replace all backups')
  await expect(execute).toBeDisabled()
  await dialog.getByLabel(/Type REPLACE ALL BACKUPS/).fill('REPLACE ALL BACKUPS')
  await expect(execute).toBeEnabled()
  await execute.click()

  await expect
    .poll(() => writes.filter((item) => item.path === '/api/v1/backups/replace-all').length)
    .toBe(1)
  expect(writes.find((item) => item.path === '/api/v1/backups/replace-all')?.body).toMatchObject({
    confirmation: 'REPLACE ALL BACKUPS',
  })
})
