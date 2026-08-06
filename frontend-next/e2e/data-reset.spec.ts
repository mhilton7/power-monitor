import { expect, test, type Page } from '@playwright/test'

import { PERMISSION_CODES } from '../src/access/permissions'

const home = {
  id: 'home-1',
  name: 'Upland Home',
  timezone: 'America/Los_Angeles',
  currency: 'USD',
  lifecycle_state: 'active',
  is_default: true,
  revision: 4,
}

const plan = {
  protocol: 'data-reset/1.0.0',
  plan_id: 'plan-11111111',
  site: home,
  categories: ['measurement_history', 'cost_history', 'pricing_history', 'generated_outputs'],
  delete_imported_bill_documents: false,
  disconnected_sensor_policy: 'defer_until_reconnect',
  reset_timestamp: '2026-08-06T18:00:00Z',
  reset_generation: 8,
  counts: {
    raw_readings: 1200,
    normalized_intervals: 360,
    daily_device_rollups: 20,
    monthly_device_rollups: 4,
    site_rollups: 12,
    device_heartbeats: 80,
    sequence_gaps: 2,
    cost_calculation_runs: 2,
    cost_interval_results: 350,
    daily_cost_rollups: 20,
    tier_allocation_segments: 18,
    cycle_tier_summaries: 2,
    tier_projection_snapshots: 3,
    billing_cycles: 2,
    rate_assignments: 2,
    imported_bill_documents: 0,
    exports: 3,
    reports: 1,
  },
  estimated_database_bytes: 2_048_000,
  estimated_sensor_records: 250,
  sensor_records_to_delete_now: 250,
  participants: [{
    device_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    name: 'Outdoor AC',
    classification: 'connected',
    supported: true,
    boundary: 5500,
    estimated_sensor_records: 250,
    local_record_count: 250,
    backlog_estimate: 12,
    record_count_status: 'exact_prepare_projection',
    last_seen_at: '2026-08-06T17:59:00Z',
    firmware_version: '1.0.18',
    firmware_build_hash: 'build-a',
    data_generation: 7,
    server_highest_contiguous: 5488,
    server_maximum_seen: 5500,
    sensor_ack_sequence: 5488,
    sensor_newest_sequence: 5500,
    old_sequence_floor: 1,
    old_next_sequence: 5501,
    card_generation: 'card-a',
  }, {
    device_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    name: 'Indoor AC',
    classification: 'disconnected',
    supported: true,
    boundary: 900,
    estimated_sensor_records: 160,
    local_record_count: 160,
    backlog_estimate: 20,
    record_count_status: 'last_reported',
    firmware_version: '1.0.18',
    data_generation: 7,
    server_highest_contiguous: 880,
    server_maximum_seen: 900,
    sensor_ack_sequence: 880,
    sensor_newest_sequence: 900,
    old_sequence_floor: 1,
    old_next_sequence: 901,
    card_generation: 'card-b',
  }],
  pricing: [{
    utility_account_id: 'account-1',
    utility_account_name: 'SCE electric service',
    rate_plan_id: 'rate-plan-1',
    rate_plan_name: 'TOU-D-PRIME',
    rate_version_id: 'rate-version-4',
    rate_assignment_id: 'assignment-1',
    pricing_configuration_hash: 'a'.repeat(64),
  }],
  preserved: [
    'users_roles_sessions_mfa',
    'site_circuits_aggregates_devices',
    'device_uuid_credentials_network_configuration',
    'current_utility_accounts_and_active_pricing',
  ],
  confirmation_phrases: {
    verified_backup: 'RESET ALL READINGS AND PRICING HISTORY',
    permanent_without_backup: 'PERMANENTLY RESET ALL READINGS AND PRICING HISTORY WITHOUT BACKUP',
  },
  fingerprint: 'b'.repeat(64),
  revision: 1,
  created_at: '2026-08-06T18:00:00Z',
  expires_at: '2099-08-06T18:15:00Z',
}

const operation = {
  protocol: 'data-reset/1.0.0',
  operation_id: 'operation-11111111',
  plan_id: plan.plan_id,
  site_id: home.id,
  state: 'completed_with_resets_pending_on_reconnect',
  stage: 'completed_with_resets_pending_on_reconnect',
  revision: 11,
  reset_generation: 8,
  reset_timestamp: plan.reset_timestamp,
  backup: {
    mode: 'verified_backup',
    backup_id: 'backup-11111111',
    reference: 'backup-11111111',
    manifest_hash: 'c'.repeat(64),
    verified_at: '2026-08-06T18:02:00Z',
    recoverable: true,
  },
  recoverability: 'verified_backup',
  participants: [{
    device_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    name: 'Outdoor AC',
    state: 'verified',
    reset_generation: 8,
    reset_boundary: 5500,
    new_sequence_floor: 5501,
    new_next_sequence: 5501,
    firmware_version: '1.0.18',
    prepared_at: '2026-08-06T18:01:00Z',
    committed_at: '2026-08-06T18:03:00Z',
    verified_at: '2026-08-06T18:04:00Z',
  }, {
    device_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    name: 'Indoor AC',
    state: 'pending_reconnect',
    reset_generation: 8,
    reset_boundary: 900,
    new_sequence_floor: null,
    new_next_sequence: null,
    firmware_version: '1.0.18',
  }],
  started_at: '2026-08-06T18:00:30Z',
  central_commit_at: '2026-08-06T18:02:30Z',
  completed_at: '2026-08-06T18:04:00Z',
  final_evidence: {
    deleted_counts: { ...plan.counts, pricing_baselines: 1 },
    pricing_hashes: { 'account-1': 'a'.repeat(64) },
  },
}

async function mockApplication(page: Page, permissions: string[], writes: Array<{ path: string; body: Record<string, unknown> }>) {
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname
    if (request.method() !== 'GET') {
      writes.push({ path: pathname, body: request.postDataJSON() as Record<string, unknown> })
      if (pathname === '/api/v1/system/data-reset/plan') return route.fulfill({ json: plan })
      if (pathname === '/api/v1/auth/reauthenticate') return route.fulfill({ json: { reauthenticated: true } })
      if (pathname === '/api/v1/system/data-reset/execute') return route.fulfill({ status: 202, json: operation })
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
          permissions,
          all_sites: true,
          site_ids: [],
          access_revision: 5,
          mfa_enabled: false,
        },
      },
      '/api/v1/sites': [home],
      '/api/v1/devices': [],
      '/api/v1/utility-accounts': [],
      '/api/v1/configuration-status': {
        schema_version: 'configuration-status/1.0', home_id: home.id, state: 'waiting_for_data', label: 'Waiting for data', summary: 'No live readings.', generated_at: '2026-08-06T18:00:00Z', issues: [],
      },
      '/api/v1/electric-services/default/current-rate-assignment': {
        schema_version: 'current-rate-assignment/1.0', home_id: home.id, electric_service_id: null, assignment: null,
      },
      '/api/v1/fleet/summary': {
        current_load_w: null, energy_today_kwh: null, estimated_cost_today: null, reporting_devices: 0, active_alerts: 0, recent_peak_w: null, latest_data_at: null, has_live_data: false, has_energy_data: false, has_cost_data: false,
      },
      '/api/v1/notifications': { items: [], page: 1, page_size: 200, total: 0 },
      '/api/v1/test-mode': {
        enabled: false, remaining_seconds: 0, sensor_count: 0, online_sensors: 0, offline_sensors: 0, sample_interval_seconds: 5, cost_preview_enabled: false, current_power_w: '0', total_energy_kwh: '0', source_type: 'simulated', environment: 'test_mode', isolation: { real_readings: true, bills_and_finalized_costs: true, exports_and_backups: true, alerts: true, credentials_and_firmware: true },
      },
      '/api/v1/system/health': {
        status: 'healthy', checked_at: '2026-08-06T18:00:00Z', api_version: '1.0.0', frontend_version: '1.0.0', compatibility: 'compatible', components: [], recent_events: [],
      },
      [`/api/v1/system/data-reset/${operation.operation_id}`]: operation,
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

test('administrator completes the guarded plan and execution flow while disconnected sensors remain explicit', async ({ page }) => {
  const writes: Array<{ path: string; body: Record<string, unknown> }> = []
  await mockApplication(page, [...PERMISSION_CODES], writes)
  await page.goto('/settings/advanced/data-reset')

  await expect(page.getByRole('heading', { name: 'Reset readings and pricing history' })).toBeVisible()
  await expect(page.getByText('Network, security, enrollment, users, and hardware settings will be preserved.')).toBeVisible()
  await expect(page.getByRole('checkbox', { name: /Also permanently delete imported/ })).not.toBeChecked()
  await page.getByRole('button', { name: 'Create read-only dry-run plan' }).click()
  await expect(page.getByText('Outdoor AC')).toBeVisible()
  await expect(page.getByText('Pending reset on reconnect')).toBeVisible()
  await page.getByRole('button', { name: 'Continue to backup choice' }).click()
  await expect(page.getByRole('radio', { name: /Create and verify backup/ })).toBeChecked()
  await page.getByRole('button', { name: 'Review and authorize' }).click()
  await page.getByLabel('Exact confirmation phrase').fill(plan.confirmation_phrases.verified_backup)
  await page.getByLabel('Audit reason').fill('Remove corrupted historical readings')
  await page.getByRole('button', { name: 'Verify identity and start reset' }).click()
  await page.getByLabel('Current password').fill('example-password')
  await page.getByRole('button', { name: 'Authorize data reset' }).click()

  await expect(page.locator('.data-reset-operation-state .pill').getByText('Completed with resets pending on reconnect', { exact: true })).toBeVisible()
  await expect(page.getByText(/Old-generation uploads remain blocked/)).toBeVisible()
  await expect(page.getByText('backup-11111111')).toBeVisible()
  await expect(page.getByText('Indoor AC').first()).toBeVisible()
  const execute = writes.find((item) => item.path === '/api/v1/system/data-reset/execute')
  expect(execute?.body).toMatchObject({
    plan_id: plan.plan_id,
    backup_mode: 'verified_backup',
    confirmation_phrase: plan.confirmation_phrases.verified_backup,
    permanent_without_backup_acknowledged: false,
  })
})

test('administrator without system.data_reset cannot see or call data-reset controls', async ({ page }) => {
  const writes: Array<{ path: string; body: Record<string, unknown> }> = []
  await mockApplication(page, ['settings.manage'], writes)
  await page.goto('/settings/advanced/data-reset')

  await expect(page.getByRole('heading', { name: 'Reset readings and pricing history' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Create read-only dry-run plan' })).toHaveCount(0)
  expect(writes.some((item) => item.path.startsWith('/api/v1/system/data-reset'))).toBe(false)
})
