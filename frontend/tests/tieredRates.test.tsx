import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CostsPage } from '../src/pages/CostsPage'
import { UtilityAccountsPanel } from '../src/components/UtilityAccountsPanel'
import { RateEditorPage } from '../src/pages/RateEditorPage'
import { UsagePage } from '../src/pages/UsagePage'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function response(value: unknown) {
  return Promise.resolve(new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  }))
}

function wrapper(children: ReactNode, path = '/') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </MemoryRouter>,
  )
}

const account = {
  id: 'account-1',
  site_id: 'site-1',
  site_name: 'Upland Site',
  utility_id: 'sce',
  utility_name: 'Southern California Edison',
  name: 'Main electric account',
  status: 'active',
  timezone: 'America/Los_Angeles',
  currency: 'USD',
  billing_cycle_start_day: 22,
  generation_provider: 'sce',
  provider_mode: 'sce_bundled',
  cost_scope: 'full_account_estimate',
  full_account_override: true,
  revision: 1,
  assignment_count: 1,
  device_count: 2,
  readiness: { rate: 'rate_configured_effective', cost: 'cost_calculation_ready', topology_complete: true },
  rate_context: { state: 'rate_configured_effective', current_currency: 'USD' },
}

const tierStatus = {
  available: true,
  utility_account_id: account.id,
  account_name: account.name,
  currency: 'USD',
  pricing_model: 'tiered',
  rate_version_id: 'version-1',
  rate_version: 2,
  cycle: {
    id: 'cycle-1',
    starts_at: '2026-07-22T07:00:00Z',
    ends_at: '2026-08-20T07:00:00Z',
    days: 29,
    days_remaining: 20,
    status: 'confirmed',
    boundary_source: 'manual_override',
    exact_dates: true,
  },
  authoritative_usage_kwh: '951',
  usage_authority: {
    configured: true,
    authority_type: 'service_leg_pair',
    complete_account: true,
    confidence: 'high',
    device_ids: ['device-a', 'device-b'],
    revision: 2,
  },
  current_tier: {
    tier_id: 'tier-2',
    name: 'Tier 2',
    order: 1,
    lower_bound_kwh: '579',
    price_per_kwh: '0.40',
    threshold_basis: 'fixed_cycle_kwh',
    rounding_policy: 'none',
  },
  remaining_kwh: null,
  tiers: [{
    tier_id: 'tier-1',
    name: 'Tier 1',
    order: 0,
    lower_bound_kwh: '0',
    upper_bound_kwh: '579',
    price_per_kwh: '0.30',
    threshold_basis: 'fixed_cycle_kwh',
    rounding_policy: 'none',
    usage_kwh: '579',
    energy_charge: '173.70',
  }, {
    tier_id: 'tier-2',
    name: 'Tier 2',
    order: 1,
    lower_bound_kwh: '579',
    price_per_kwh: '0.40',
    threshold_basis: 'fixed_cycle_kwh',
    rounding_policy: 'none',
    usage_kwh: '372',
    energy_charge: '148.80',
  }],
  energy_charge: '322.50',
  current_rate_period: 'Tier 2',
  current_energy_price: '0.40',
  blended_energy_rate: '0.3391167192429022082018927445',
  projected_usage_kwh: '1200',
  projected_energy_charge: '422.10',
  projected_final_tier: { tier_id: 'tier-2', name: 'Tier 2', order: 1, lower_bound_kwh: '579', price_per_kwh: '0.40', threshold_basis: 'fixed_cycle_kwh', rounding_policy: 'none' },
  projection_method: 'straight_line',
  projection_confidence: 'medium',
  coverage_percent: '100',
  bill_components: {
    energy_charge: '322.50',
    fixed_charge: '11.00',
    credits: '-5.00',
    adjustments: '2.00',
    estimated_total: '330.50',
    projected_total: '430.10',
    scope: 'full_account_estimate',
  },
  estimated_total_bill: '330.50',
  projected_total_bill: '430.10',
  recalculation_version: 4,
  warnings: [],
  disclosure: 'Energy charges are chronologically allocated estimates.',
}

function mockTierApi() {
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (url.endsWith('/api/v1/utility-accounts')) return response([account])
    if (url.endsWith('/tier-status')) return response(tierStatus)
    return response({})
  }))
}

describe('tiered and hybrid rate interface', () => {
  it('shows exact usage-by-tier progress and projection evidence', async () => {
    mockTierApi()
    wrapper(<UsagePage />)
    expect(await screen.findByText('Usage by tier')).toBeVisible()
    expect(screen.getAllByText('Tier 2').length).toBeGreaterThan(0)
    expect(screen.getAllByText('951 kWh').length).toBeGreaterThan(0)
    expect(screen.getByText('$173.70')).toBeVisible()
    expect(screen.getByText('$148.80')).toBeVisible()
    expect(screen.getByText('$0.40/kWh')).toBeVisible()
    expect(screen.getByText(/blended cycle energy rate \$0\.3391\/kWh/i)).toBeVisible()
    expect(screen.getByText(/service leg pair/i)).toBeVisible()
  })

  it('keeps energy subtotal separate from complete-account bill components', async () => {
    mockTierApi()
    wrapper(<CostsPage />)
    expect(await screen.findByText('Energy charge by tier')).toBeVisible()
    expect(screen.getAllByText('$322.50').length).toBeGreaterThan(0)
    expect(screen.getAllByText('$330.50').length).toBeGreaterThan(0)
    expect(screen.getByText('Bill components')).toBeVisible()
    expect(screen.getByText(/Estimate, not utility bill/i)).toBeVisible()
  })

  it('provides arbitrary tier, daily baseline, and hybrid editing controls', () => {
    wrapper(
      <Routes>
        <Route path="/rates/new" element={<RateEditorPage canManage />} />
      </Routes>,
      '/rates/new',
    )
    fireEvent.click(screen.getByRole('button', { name: /pricing & tiers/i }))
    expect(screen.getByLabelText('Pricing model')).toBeVisible()
    fireEvent.change(screen.getByLabelText('Pricing model'), { target: { value: 'tiered' } })
    expect(screen.getByRole('button', { name: /add tier/i })).toBeVisible()
    expect(screen.getByLabelText('Threshold basis')).toBeVisible()
    fireEvent.change(screen.getByLabelText('Pricing model'), { target: { value: 'time_of_use_tiered' } })
    expect(screen.getByLabelText('Hybrid calculation method')).toBeVisible()
  })

  it('previews and commits normalized utility usage evidence', async () => {
    const requests: Array<Record<string, unknown>> = []
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (url.endsWith('/api/v1/admin/sites/site-1/utility-accounts')) return response([account])
      if (url.endsWith('/api/v1/sites/site-1/setup-readiness')) return response({
        monitoring: { state: 'ready', device_count: 2 },
        rate_and_cost: { state: 'ready', cost_state: 'ready', account_count: 1, effective_account_count: 1, cost_ready_account_count: 1, pending_candidate_count: 0 },
      })
      if (url.endsWith('/api/v1/rates/plans')) return response([])
      if (url.endsWith('/rate-assignments') || url.endsWith('/adjustments')) return response([])
      if (url.endsWith('/usage-authority')) return response(tierStatus.usage_authority)
      if (url.endsWith('/api/v1/devices') || url.endsWith('/api/v1/aggregate-sets')) return response([])
      if (url.endsWith('/tier-status')) return response(tierStatus)
      if (url.endsWith('/usage-imports')) {
        if (typeof init?.body !== 'string') throw new Error('expected JSON request body')
        const body = JSON.parse(init.body) as Record<string, unknown>
        requests.push(body)
        return response({
          content_sha256: 'a'.repeat(64),
          row_count: 1,
          duplicate: false,
          conflict_count: 0,
          duplicate_row_count: 0,
          overlap_count: 0,
          gap_count: 0,
          affected_cycle_count: 1,
          finalized_cycle_conflict: false,
          normalized_preview: [{ start: '2026-07-22T07:00:00+00:00', end: '2026-07-22T08:00:00+00:00', energy_kwh: '1.25' }],
          will_commit: body.commit,
          status: body.commit ? 'committed' : undefined,
        })
      }
      return response({})
    }))

    wrapper(<UtilityAccountsPanel sites={[{ id: 'site-1', name: 'Upland Site', timezone: 'America/Los_Angeles', allowed_cidrs: [], allowed_domains: [], allow_public_polling: false }]} />)
    fireEvent.click(await screen.findByRole('button', { name: /view details/i }))
    fireEvent.change(await screen.findByLabelText('Source name'), { target: { value: 'Utility portal export' } })
    fireEvent.change(screen.getByLabelText('Usage rows (JSON)'), {
      target: { value: '[{"start":"2026-07-22T00:00:00-07:00","end":"2026-07-22T01:00:00-07:00","energy_kwh":"1.25"}]' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Preview and validate' }))
    expect(await screen.findByText(/Import preview normalized/i)).toBeVisible()
    const commit = screen.getByRole('button', { name: 'Commit reviewed evidence' })
    await waitFor(() => { expect(commit).toBeEnabled() })
    fireEvent.click(commit)
    expect(await screen.findByText(/Usage evidence committed/i)).toBeVisible()
    expect(requests.map((item) => item.commit)).toEqual([false, true])
  })
})
