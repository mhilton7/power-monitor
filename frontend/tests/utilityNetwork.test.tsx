import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { NetworkPolicyPanel } from '../src/components/NetworkPolicyPanel'
import { UtilityAccountsPanel } from '../src/components/UtilityAccountsPanel'
import type { Site } from '../src/types'

const site: Site = {
  id: 'site-1',
  name: 'Upland Site',
  timezone: 'America/Los_Angeles',
  allowed_cidrs: [],
  allowed_domains: [],
  allow_public_polling: false,
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

function requestUrl(input: RequestInfo | URL) {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.href
  return input.url
}

function response(value: unknown, status = 200) {
  return Promise.resolve(new Response(status === 204 ? null : JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  }))
}

function wrapper(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<MemoryRouter><QueryClientProvider client={client}>{children}</QueryClientProvider></MemoryRouter>)
}

describe('utility account and network administration', () => {
  it('turns the utility-account empty state into the seven-step persisted workflow', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = requestUrl(input)
      if (url.endsWith('/api/v1/rates/plans')) return response([{
        id: 'plan-1', code: 'TOU-D-4-9PM', name: 'TOU-D 4 PM to 9 PM', description: 'SCE residential TOU', plan_kind: 'official_sce', ownership_scope: 'global', currency: 'USD', timezone: site.timezone, status: 'active', versions: [{ id: 'version-1', version: 1, effective_from: '2026-06-01', status: 'active', source_kind: 'official_sce', source_checked_at: '2026-07-20T00:00:00Z', source_label: 'SCE source', integrity_sha256: '1234567890abcdef', is_active: true, immutable: true, created_at: '2026-07-20T00:00:00Z' }],
      }])
      if (url.endsWith('/api/v1/sites/site-1/setup-readiness')) return response({ monitoring: { state: 'no_sensors_enrolled', device_count: 0 }, rate_and_cost: { state: 'no_utility_account', cost_state: 'cost_calculation_blocked_invalid_configuration', account_count: 0, effective_account_count: 0, cost_ready_account_count: 0, pending_candidate_count: 0 } })
      if (url.includes('/api/v1/admin/sites/site-1/utility-accounts')) return response([])
      return response([])
    }))
    wrapper(<UtilityAccountsPanel sites={[site]} />)
    expect(await screen.findByText('No utility account configured')).toBeVisible()
    const createButton = screen.getAllByRole('button', { name: /create utility account/i }).at(0)
    expect(createButton).toBeDefined()
    if (createButton) fireEvent.click(createButton)
    expect(screen.getByRole('dialog', { name: 'Utility Setup' })).toBeVisible()
    expect(screen.queryByText('Account identity')).not.toBeInTheDocument()
    expect(screen.getByText('Step 1 of 7')).toBeVisible()
    expect(screen.getByText('Review & create')).toBeVisible()
    fireEvent.change(screen.getByLabelText('Account display name'), { target: { value: 'Main electric account' } })
    fireEvent.click(screen.getByRole('button', { name: /next/i }))
    expect(screen.getByRole('dialog', { name: 'Utility & provider' })).toBeVisible()
    expect(screen.getByText(/CCA and Direct Access generation prices/i)).toBeVisible()
  })

  it('renders account readiness and persists edit, rate, scope, adjustment, and archive actions', async () => {
    const mutations: string[] = []
    const account = {
      id: 'account-1', site_id: site.id, site_name: site.name, utility_id: 'sce', utility_name: 'Southern California Edison', name: 'Main electric account', status: 'active', timezone: site.timezone, currency: 'USD', billing_cycle_start_day: 17, generation_provider: 'sce', provider_mode: 'sce_bundled', service_class: 'Residential', cost_scope: 'energy_only', full_account_override: false, revision: 1, assignment_count: 1, device_count: 0,
      readiness: { rate: 'rate_configured_effective', cost: 'cost_blocked_missing_readings', topology_complete: false },
      rate_context: { state: 'rate_configured_effective', current_plan: 'TOU-D 4 PM to 9 PM', current_version: 3, current_period: 'On Peak', current_price_per_kwh: '0.58000000', next_period: 'Off Peak', next_price_per_kwh: '0.24000000', current_currency: 'USD', assignment_effective_from: '2026-06-01T00:00:00Z' },
    }
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      if (url.endsWith('/api/v1/rates/plans')) return response([{ id: 'plan-1', code: 'TOU-D-4-9PM', name: 'TOU-D 4 PM to 9 PM', plan_kind: 'official_sce', status: 'active', versions: [{ id: 'version-1', version: 3, effective_from: '2026-06-01', status: 'active', source_kind: 'official_sce', source_checked_at: '2026-07-20T00:00:00Z', source_label: 'SCE evidence', integrity_sha256: 'abcdef1234567890', is_active: true, immutable: true, created_at: '2026-06-01T00:00:00Z' }] }])
      if (url.endsWith('/api/v1/sites/site-1/setup-readiness')) return response({ monitoring: { state: 'waiting_for_first_signed_heartbeat', device_count: 1 }, rate_and_cost: { state: 'rate_configured_effective', cost_state: 'cost_calculation_blocked_missing_readings', account_count: 1, effective_account_count: 1, cost_ready_account_count: 0, pending_candidate_count: 0 } })
      if (url.endsWith('/api/v1/admin/sites/site-1/utility-accounts')) return response([account])
      if (url.endsWith('/rate-assignments') && init?.method !== 'POST') return response([{ id: 'assignment-1', plan_code: 'TOU-D-4-9PM', plan_name: 'TOU-D 4 PM to 9 PM', version: 3, effective_from: '2026-06-01T00:00:00Z', assignment_reason: 'Initial setup' }])
      if (url.endsWith('/adjustments') && init?.method !== 'POST') return response([])
      if (init?.method && init.method !== 'GET') { mutations.push(`${init.method} ${url}`); return response(url.endsWith('/recalculate') ? { queued_runs: 1 } : url.endsWith('/archive') ? { ...account, status: 'archived' } : account, url.endsWith('/adjustments') || url.endsWith('/rate-assignments') ? 201 : 200) }
      return response([])
    }))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    wrapper(<UtilityAccountsPanel sites={[site]} />)
    const card = await screen.findByText('Main electric account')
    expect(card).toBeVisible()
    expect(screen.getByText('On Peak')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Manage' }))
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Updated main account' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save account' }))
    await waitFor(() => { expect(mutations.some((item) => item.includes('PUT') && item.includes('/utility-accounts/account-1'))).toBe(true) })
    fireEvent.click(screen.getByRole('button', { name: 'Save rate assignment' }))
    fireEvent.change(screen.getByLabelText('Scope'), { target: { value: 'allocated_account_estimate' } })
    fireEvent.change(screen.getByLabelText('Allocation method'), { target: { value: 'Proportional to kWh' } })
    fireEvent.click(screen.getByRole('button', { name: 'Update cost scope' }))
    fireEvent.change(screen.getByLabelText('New custom $/kWh'), { target: { value: '0.01' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add adjustment' }))
    fireEvent.click(screen.getByRole('button', { name: 'Archive' }))
    await waitFor(() => {
      expect(mutations.some((item) => item.endsWith('/rate-assignments'))).toBe(true)
      expect(mutations.some((item) => item.endsWith('/cost-scope'))).toBe(true)
      expect(mutations.some((item) => item.endsWith('/adjustments'))).toBe(true)
      expect(mutations.some((item) => item.endsWith('/archive'))).toBe(true)
    })
  })

  it('shows explicit ingress and pull modes, legacy meaning, CIDR controls, and no-scan test', async () => {
    const policies = [{
      id: 'ingress-1', site_id: site.id, site_name: site.name, direction: 'device_ingress', mode: 'legacy_authenticated_any', revision: 1, migration_notice_pending: true, migrated_from_legacy: true, effective_summary: 'Legacy signed ingress · review required', cidrs: [],
    }, {
      id: 'pull-1', site_id: site.id, site_name: site.name, direction: 'server_pull', mode: 'deny_all', revision: 1, migration_notice_pending: true, migrated_from_legacy: true, effective_summary: 'Device network access denied', cidrs: [],
    }]
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      if (url.endsWith('/api/v1/admin/network/policies')) return response(policies)
      if (url.endsWith('/api/v1/admin/network/runtime')) return response({ sensor_server_url: 'https://power-monitor.local', tls_verification_required: true, certificate_trust: 'Internal CA', default_device_api_port: 443, communication_modes: ['push', 'pull', 'hybrid'], mdns_authoritative: false, heartbeat_expectation_seconds: 15, stale_device_seconds: 60, server_time: '2026-07-21T12:00:00Z', server_timezone: 'UTC', trusted_forwarded_headers: true, address_source: 'Signed device heartbeat' })
      if (url.includes('/api/v1/admin/network/observed-devices')) return response([])
      if (url.endsWith('/api/v1/admin/network/test-address') && init?.method === 'POST') return response({ allowed: false, address: '192.168.60.10', direction: 'server_pull', mode: 'deny_all', reason: 'Device network access is locked down' })
      return response({})
    }))
    wrapper(<NetworkPolicyPanel sites={[site]} />)
    expect(await screen.findByText('Legacy signed ingress · review required')).toBeVisible()
    expect(screen.getByText('Legacy behavior was preserved exactly')).toBeVisible()
    expect(screen.getByRole('radio', { name: /Allow listed private networks only/i })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'Server pull access' }))
    expect(await screen.findByText('Device network access denied')).toBeVisible()
    expect(screen.getByText(/explicitly means server\/device access/i)).toBeVisible()
    fireEvent.change(screen.getByLabelText('Address'), { target: { value: '192.168.60.10' } })
    fireEvent.click(screen.getByRole('button', { name: 'Test address' }))
    await waitFor(() => expect(screen.getByText('Blocked')).toBeVisible())
    expect(screen.getByText('This evaluates policy only. It does not connect to or scan the address.')).toBeVisible()
  })
})
