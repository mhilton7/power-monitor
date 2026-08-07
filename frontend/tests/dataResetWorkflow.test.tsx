import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, request } from '../src/api/client'
import { DataResetWorkflow } from '../src/features/data-reset/DataResetWorkflow'

vi.mock('../src/api/client', async (importActual) => {
  const actual = await importActual<typeof import('../src/api/client')>()
  return { ...actual, request: vi.fn() }
})

const requestMock = vi.mocked(request)

const planResponse = {
  protocol: 'data-reset/1.0.0',
  plan_id: 'plan-11111111',
  site: { id: 'home-1', name: 'Upland Home', revision: 4, timezone: 'America/Los_Angeles' },
  categories: ['measurement_history', 'cost_history', 'pricing_history', 'generated_outputs'],
  delete_imported_bill_documents: false,
  disconnected_sensor_policy: 'defer_until_reconnect',
  reset_timestamp: '2026-08-06T18:00:00Z',
  reset_generation: 7,
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
  participants: [
    {
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
      data_generation: 6,
      server_highest_contiguous: 5488,
      server_maximum_seen: 5500,
      sensor_ack_sequence: 5488,
      sensor_newest_sequence: 5500,
      old_sequence_floor: 1,
      old_next_sequence: 5501,
      card_generation: 'card-a',
    },
    {
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
      data_generation: 6,
      server_highest_contiguous: 880,
      server_maximum_seen: 900,
      sensor_ack_sequence: 880,
      sensor_newest_sequence: 900,
      old_sequence_floor: 1,
      old_next_sequence: 901,
      card_generation: 'card-b',
    },
  ],
  pricing: [{
    utility_account_id: 'account-1',
    utility_account_name: 'SCE electric service',
    rate_plan_id: 'plan-rate-1',
    rate_plan_name: 'TOU-D-PRIME',
    rate_version_id: 'rate-version-4',
    rate_assignment_id: 'assignment-1',
    pricing_configuration_hash: 'a'.repeat(64),
  }],
  preserved: [
    'users_roles_sessions_mfa',
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

const completedOperation = {
  protocol: 'data-reset/1.0.0',
  operation_id: 'operation-11111111',
  plan_id: planResponse.plan_id,
  site_id: 'home-1',
  state: 'completed',
  stage: 'completed',
  revision: 12,
  reset_generation: 7,
  reset_timestamp: '2026-08-06T18:00:00Z',
  backup: {
    mode: 'permanent_without_backup',
    backup_id: null,
    reference: null,
    manifest_hash: null,
    verified_at: null,
    recoverable: false,
  },
  recoverability: 'irreversible_no_backup',
  participants: [{
    device_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    name: 'Outdoor AC',
    state: 'verified',
    reset_generation: 7,
    reset_boundary: 5500,
    new_sequence_floor: 5501,
    new_next_sequence: 5501,
    firmware_version: '1.0.18',
    prepared_at: '2026-08-06T18:01:00Z',
    committed_at: '2026-08-06T18:03:00Z',
    verified_at: '2026-08-06T18:04:00Z',
  }],
  started_at: '2026-08-06T18:00:30Z',
  central_commit_at: '2026-08-06T18:02:00Z',
  completed_at: '2026-08-06T18:04:00Z',
  failure_code: null,
  failure_summary: null,
  final_evidence: {
    deleted_counts: { ...planResponse.counts, pricing_baselines: 1 },
    pricing_hashes: { 'account-1': 'a'.repeat(64) },
    new_readings_received: true,
    new_readings_status: 'confirmed',
    new_cost_calculation_confirmed: true,
    new_cost_status: 'confirmed',
    raw_sensor_receipt: 'must-not-render',
  },
}

function renderWorkflow() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  return render(<QueryClientProvider client={client}><DataResetWorkflow siteId="home-1" siteName="Upland Home" mfaEnabled={false} /></QueryClientProvider>)
}

function installResponses() {
  requestMock.mockImplementation((path, _init, adapt) => {
    let response: unknown = {}
    if (path === '/api/v1/system/data-reset/plan') response = planResponse
    if (path === '/api/v1/system/data-reset/execute' || path === `/api/v1/system/data-reset/${completedOperation.operation_id}`) response = completedOperation
    return Promise.resolve(adapt ? adapt(response) : response)
  })
}

function bodyFor(path: string): Record<string, unknown> {
  const call = requestMock.mock.calls.find(([calledPath]) => calledPath === path)
  if (!call) throw new Error(`Request ${path} was not made`)
  const body = call[1]?.body
  if (typeof body !== 'string') throw new Error(`Request ${path} did not include a JSON string body`)
  return JSON.parse(body) as Record<string, unknown>
}

beforeEach(() => {
  requestMock.mockReset()
  localStorage.clear()
  installResponses()
})

describe('coordinated data-only reset workflow', () => {
  it('defaults to the full data-only scope, preserves imported bills, and renders exact sensor classifications', async () => {
    const user = userEvent.setup()
    renderWorkflow()

    expect(screen.getByRole('checkbox', { name: /Electrical readings/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /Electrical readings/ })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: /Also permanently delete imported/ })).not.toBeChecked()
    expect(screen.getByRole('radio', { name: /Reset on authenticated reconnect/ })).toBeChecked()

    await user.click(screen.getByRole('button', { name: 'Create read-only dry-run plan' }))
    expect(await screen.findByRole('heading', { name: '2. Review the dry-run plan' })).toBeInTheDocument()
    expect(bodyFor('/api/v1/system/data-reset/plan')).toEqual({
      site_id: 'home-1',
      categories: ['measurement_history', 'cost_history', 'pricing_history', 'generated_outputs'],
      delete_imported_bill_documents: false,
      disconnected_sensor_policy: 'defer_until_reconnect',
    })
    expect(screen.getByText('Outdoor AC')).toBeInTheDocument()
    expect(screen.getByText('Will reset now')).toBeInTheDocument()
    expect(screen.getByText('Pending reset on reconnect')).toBeInTheDocument()
    expect(screen.getByText('Connected-sensor measurement pause.')).toBeInTheDocument()
    expect(screen.getByText('TOU-D-PRIME')).toBeInTheDocument()
    expect(screen.getByText('Sensor records to delete now')).toBeInTheDocument()
    expect(screen.queryByText('410')).not.toBeInTheDocument()
  })

  it('requires the separate no-backup acknowledgement, exact API phrase, and recent reauthentication', async () => {
    const user = userEvent.setup()
    renderWorkflow()
    await user.click(screen.getByRole('button', { name: 'Create read-only dry-run plan' }))
    await user.click(await screen.findByRole('button', { name: 'Continue to backup choice' }))

    expect(screen.getByRole('radio', { name: /Create and verify backup/ })).toBeChecked()
    await user.click(screen.getByRole('radio', { name: /Permanently reset without backup/ }))
    expect(screen.getByRole('button', { name: 'Review and authorize' })).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: /I understand this reset is permanent/ }))
    await user.click(screen.getByRole('button', { name: 'Review and authorize' }))

    const phrase = planResponse.confirmation_phrases.permanent_without_backup
    expect(screen.getByText(phrase)).toBeInTheDocument()
    await user.type(screen.getByLabelText('Exact confirmation phrase'), phrase)
    await user.type(screen.getByLabelText('Audit reason'), 'Replacing corrupted historical measurements')
    await user.click(screen.getByRole('button', { name: 'Verify identity and start reset' }))

    expect(await screen.findByRole('heading', { name: 'Authorize permanent data deletion' })).toBeInTheDocument()
    await user.type(screen.getByLabelText('Current password'), 'correct horse battery staple')
    await user.click(screen.getByRole('button', { name: 'Authorize data reset' }))

    await waitFor(() => { expect(requestMock).toHaveBeenCalledWith('/api/v1/system/data-reset/execute', expect.anything(), expect.anything()) })
    expect(bodyFor('/api/v1/system/data-reset/execute')).toMatchObject({
      plan_id: planResponse.plan_id,
      plan_revision: 1,
      reason: 'Replacing corrupted historical measurements',
      backup_mode: 'permanent_without_backup',
      confirmation_phrase: phrase,
      permanent_without_backup_acknowledged: true,
    })
    expect(bodyFor('/api/v1/system/data-reset/execute').idempotency_key).toEqual(expect.any(String))
    expect(await screen.findByText('New cost calculation')).toBeInTheDocument()
    expect(screen.getAllByText('Confirmed').length).toBeGreaterThanOrEqual(2)
    expect(screen.queryByText('must-not-render')).not.toBeInTheDocument()
    expect(localStorage.getItem('pm-data-reset-operation:home-1')).toContain(completedOperation.operation_id)
  })

  it('explains why a former-site sensor with device-wide backlog blocks planning', async () => {
    requestMock.mockRejectedValueOnce(new ApiError({
      title: 'Historical sensor scope cannot be reset safely',
      detail: 'One or more sensors that recorded data for this site are now assigned elsewhere.',
      status: 409,
      code: 'data_reset_historical_device_scope_unsafe',
    }))
    const user = userEvent.setup()
    renderWorkflow()

    await user.click(screen.getByRole('button', { name: 'Create read-only dry-run plan' }))

    expect(await screen.findByText(/Resolve its device-wide SD backlog/)).toBeInTheDocument()
    expect(screen.getByText(/data_reset_historical_device_scope_unsafe:/)).toBeInTheDocument()
  })

  it('resumes a durable operation after reload using only its opaque operation ID', async () => {
    localStorage.setItem('pm-data-reset-operation:home-1', JSON.stringify({
      operationId: completedOperation.operation_id,
      participantNames: { 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa': 'Outdoor AC' },
      planCounts: planResponse.counts,
      categories: planResponse.categories,
      preserved: planResponse.preserved,
    }))
    renderWorkflow()

    expect(await screen.findByText('Completed', { selector: '.pill' })).toBeInTheDocument()
    expect(requestMock).toHaveBeenCalledWith(`/api/v1/system/data-reset/${completedOperation.operation_id}`, {}, expect.anything())
    expect(screen.getAllByText('Outdoor AC').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('5,501').length).toBeGreaterThanOrEqual(1)
    expect(screen.queryByText('must-not-render')).not.toBeInTheDocument()
  })
})
