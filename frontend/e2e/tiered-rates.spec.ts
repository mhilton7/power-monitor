import { expect, test, type Page } from '@playwright/test'

const tierStatus = {
  available: true,
  utility_account_id: 'account-1',
  account_name: 'Home utility account',
  currency: 'USD',
  pricing_model: 'tiered',
  rate_version_id: 'rate-version-tiered',
  rate_version: 2,
  cycle: {
    id: 'cycle-1',
    starts_at: '2026-07-22T07:00:00Z',
    ends_at: '2026-08-21T07:00:00Z',
    days: 30,
    days_remaining: 28,
    status: 'confirmed',
    boundary_source: 'utility_import',
    exact_dates: true,
  },
  authoritative_usage_kwh: '951',
  usage_authority: {
    configured: true,
    authority_type: 'utility_interval_import',
    complete_account: true,
    confidence: 'verified',
    source_reference: 'fixture-utility-export.csv',
    device_ids: [],
    revision: 2,
  },
  current_tier: {
    tier_id: 'tier-2',
    name: 'Tier 2',
    order: 2,
    lower_bound_kwh: '579',
    price_per_kwh: '0.40',
    threshold_basis: 'fixed_cycle_kwh',
    rounding_policy: 'none',
    usage_kwh: '372',
    energy_charge: '148.80',
  },
  current_rate_period: 'Tier 2',
  current_energy_price: '0.40',
  tiers: [
    {
      tier_id: 'tier-1',
      name: 'Tier 1',
      order: 1,
      lower_bound_kwh: '0',
      upper_bound_kwh: '579',
      price_per_kwh: '0.30',
      threshold_basis: 'fixed_cycle_kwh',
      rounding_policy: 'none',
      usage_kwh: '579',
      energy_charge: '173.70',
    },
    {
      tier_id: 'tier-2',
      name: 'Tier 2',
      order: 2,
      lower_bound_kwh: '579',
      price_per_kwh: '0.40',
      threshold_basis: 'fixed_cycle_kwh',
      rounding_policy: 'none',
      usage_kwh: '372',
      energy_charge: '148.80',
    },
  ],
  energy_charge: '322.50',
  blended_energy_rate: '0.3391167192',
  projected_usage_kwh: '1200',
  projected_energy_charge: '422.10',
  projected_total_bill: '432.10',
  projected_final_tier: {
    tier_id: 'tier-2',
    name: 'Tier 2',
    order: 2,
    lower_bound_kwh: '579',
    price_per_kwh: '0.40',
    threshold_basis: 'fixed_cycle_kwh',
    rounding_policy: 'none',
  },
  projection_method: 'daily_run_rate',
  projection_confidence: 'verified',
  coverage_percent: '100',
  bill_components: {
    energy_charge: '322.50',
    fixed_charge: '10.00',
    credits: '0',
    adjustments: '0',
    estimated_total: '332.50',
    projected_total: '432.10',
    scope: 'full_account_estimate',
  },
  estimated_total_bill: '332.50',
  recalculation_version: 3,
  warnings: [],
  disclosure: 'Calculated from exact utility-account usage and immutable rate version evidence.',
}

async function mockApplication(page: Page) {
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    let body: unknown = {}
    if (path === '/api/v1/auth/session') {
      body = {
        authenticated: true,
        bootstrap_required: false,
        user: {
          id: 'admin-1',
          email: 'admin@example.test',
          display_name: 'Administrator',
          roles: ['admin'],
        },
      }
    } else if (path === '/api/v1/public/interface-text' || path === '/api/v1/interface-text') {
      body = { revision: 0, values: {} }
    } else if (path === '/api/v1/sites') {
      body = [{
        id: 'site-1',
        name: 'Upland Site',
        timezone: 'America/Los_Angeles',
        allowed_cidrs: [],
        allowed_domains: [],
        allow_public_polling: false,
      }]
    } else if (path === '/api/v1/status-indicators/registry') {
      body = {
        registry_version: 'status-indicators/1.0',
        indicators: [],
        zones: [],
        pages: ['usage', 'costs'],
        breakpoints: ['desktop', 'tablet', 'mobile'],
      }
    } else if (path === '/api/v1/status-indicators/layout') {
      body = {
        registry_version: 'status-indicators/1.0',
        page: 'usage',
        breakpoint: 'desktop',
        role: 'admin',
        revision: 1,
        zones: [],
        warnings: [],
      }
    } else if (path === '/api/v1/utility-accounts') {
      body = [{
        id: 'account-1',
        site_id: 'site-1',
        name: 'Home utility account',
        status: 'active',
      }]
    } else if (path === '/api/v1/utility-accounts/account-1/tier-status') {
      body = tierStatus
    } else if (path === '/api/v1/fleet/summary') {
      body = { active_alerts: 0, current_load_w: '0', total_devices: 0 }
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
  })
}

test('renders authoritative tier usage and exact account cost evidence', async ({ page }) => {
  await mockApplication(page)

  await page.goto('/analytics/usage')
  await expect(page.getByRole('heading', { name: 'Analytics', exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Usage' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('heading', { name: 'Usage by tier' })).toBeVisible()
  await expect(page.getByText('951 kWh', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('cell', { name: '372 kWh' })).toBeVisible()
  await expect(page.getByText('Tier 2', { exact: true }).first()).toBeVisible()

  await page.goto('/analytics/costs')
  await expect(page.getByRole('heading', { name: 'Analytics', exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Costs' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('heading', { name: 'Energy charge by tier' })).toBeVisible()
  await expect(page.getByText('$322.50', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Estimate, not utility bill.')).toBeVisible()
  await expect(page.getByText('v2 (rate-version-tiered)')).toBeVisible()
})
