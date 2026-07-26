import { expect, test } from '@playwright/test'

test('Set as current updates Billing, Home, History, and configuration without reload', async ({ page }) => {
  let current = false
  let documentRequests = 0
  page.on('request', (request) => {
    if (request.resourceType() === 'document') documentRequests += 1
  })
  await page.addInitScript(() => {
    localStorage.setItem('pm-single-home-onboarding-complete', 'true')
  })
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 })
    if (path === '/api/v1/auth/session') {
      return route.fulfill({ json: {
        authenticated: true,
        bootstrap_required: false,
        user: {
          id: 'owner-1',
          email: 'owner@example.test',
          display_name: 'Home Owner',
          roles: ['admin'],
          permissions: ['sites.view', 'utility_accounts.view', 'rates.view', 'rates.assign', 'rates.manage_custom'],
          all_sites: true,
          site_ids: [],
        },
      } })
    }
    if (path === '/api/v1/sites') {
      return route.fulfill({ json: [{
        id: 'home-1',
        name: 'Upland Home',
        timezone: 'America/Los_Angeles',
        currency: 'USD',
        lifecycle_state: 'active',
        is_default: true,
        revision: 1,
      }] })
    }
    if (path === '/api/v1/devices') {
      return route.fulfill({ json: [{
        id: 'sensor-1',
        name: 'Main panel',
        status: 'online_synchronized',
        current_watts: '1420',
        last_seen_at: '2026-07-25T12:00:00Z',
        measurement_role: 'full_account',
      }] })
    }
    if (path === '/api/v1/utility-accounts') {
      return route.fulfill({ json: [{
        id: 'service-1',
        site_id: 'home-1',
        name: 'Home-Energy',
        nickname: 'Home-Energy',
        utility_name: 'Southern California Edison',
        status: 'active',
        timezone: 'America/Los_Angeles',
        currency: 'USD',
        billing_cycle_start_day: 1,
        cost_scope: 'energy_only',
        revision: current ? 2 : 1,
        readiness: {
          rate: current ? 'rate_configured_effective' : 'no_rate_assignment',
          cost: current ? 'cost_calculation_ready' : 'cost_blocked_rate_setup',
          topology_complete: true,
        },
        rate_context: {
          state: current ? 'rate_configured_effective' : 'no_rate_assignment',
          current_plan: current ? 'New Current Plan' : null,
          plan_code: current ? 'NEW-CURRENT' : null,
          rate_version_id: current ? 'version-new' : null,
          current_version: current ? 2 : null,
          current_assignment_id: current ? 'assignment-new' : null,
          current_assignment_revision: current ? 1 : null,
          current_period: current ? 'Flat rate' : null,
          current_price_per_kwh: current ? '0.31000000' : null,
          billing_cycle: {
            starts_at: '2026-07-01T07:00:00Z',
            ends_at: '2026-08-01T07:00:00Z',
          },
        },
      }] })
    }
    if (path === '/api/v1/utility-accounts/service-1/tier-status') {
      return route.fulfill({ json: {
        available: current,
        cycle: {
          starts_at: '2026-07-01T07:00:00Z',
          ends_at: '2026-08-01T07:00:00Z',
          days_remaining: 6,
        },
        current_rate_period: current ? 'Flat rate' : null,
        current_energy_price: current ? '0.31000000' : null,
        authoritative_usage_kwh: current ? '42.5' : null,
        energy_charge: current ? '13.175' : null,
        coverage_percent: '100',
        tiers: [],
        warnings: [],
      } })
    }
    if (path === '/api/v1/configuration-status') {
      return route.fulfill({ json: current ? {
        schema_version: 'configuration-status/1.0',
        home_id: 'home-1',
        electric_service_id: 'service-1',
        state: 'ready',
        label: 'Ready',
        summary: 'Configuration is complete.',
        generated_at: '2026-07-25T12:00:00Z',
        issues: [],
      } : {
        schema_version: 'configuration-status/1.0',
        home_id: 'home-1',
        electric_service_id: 'service-1',
        state: 'setup_needed',
        label: 'Setup needed',
        summary: '1 blocking and 0 advisory issues.',
        generated_at: '2026-07-25T12:00:00Z',
        issues: [{
          id: 'rate-assignment.missing',
          category: 'rate_plan',
          state: 'setup_needed',
          title: 'Choose a current rate plan',
          what_is_wrong: 'No plan is effective now.',
          why_it_matters: 'Costs are unavailable.',
          how_to_fix: 'Choose a published version.',
          blocking: true,
          action: {
            id: 'rate_assignment.make_current',
            label: 'Choose current plan',
            target: '/billing?advanced=rates&tab=versions',
          },
        }],
      } })
    }
    if (path === '/api/v1/electric-services/default/current-rate-assignment') {
      return route.fulfill({ json: {
        schema_version: 'current-rate-assignment/1.0',
        home_id: 'home-1',
        electric_service_id: 'service-1',
        service_revision: current ? 2 : 1,
        assignment: current ? {
          assignment_id: 'assignment-new',
          assignment_revision: 1,
          plan_id: 'plan-new',
          plan_code: 'NEW-CURRENT',
          plan_name: 'New Current Plan',
          version_id: 'version-new',
          version: 2,
          pricing_model: 'flat',
          effective_from: '2026-07-25T12:00:00Z',
          effective_to: null,
          state: 'current',
        } : null,
      } })
    }
    if (path === '/api/v1/rates/plans') {
      return route.fulfill({ json: [{
        id: 'plan-new',
        name: 'New Current Plan',
        code: 'NEW-CURRENT',
        status: 'active',
        lifecycle_revision: 1,
        versions: [{
          id: 'version-new',
          version: 2,
          publication_status: 'published',
          assignment_status: current ? 'current' : 'unassigned',
          display_status: current ? 'current' : 'published',
          pricing_model: 'flat',
          lifecycle_revision: 1,
          assignments: current ? [{
            id: 'assignment-new',
            utility_account_id: 'service-1',
            rate_version_id: 'version-new',
            effective_from: '2026-07-25T12:00:00Z',
            state: 'current',
            revision: 1,
          }] : [],
        }],
      }] })
    }
    if (path === '/api/v1/rates/plans/plan-new/versions') {
      return route.fulfill({ json: [{
        id: 'version-new',
        version: 2,
        publication_status: 'published',
        assignment_status: current ? 'current' : 'unassigned',
        display_status: current ? 'current' : 'published',
        pricing_model: 'flat',
        lifecycle_revision: 1,
        effective_from: '2026-07-01',
        assignments: current ? [{
          id: 'assignment-new',
          utility_account_id: 'service-1',
          rate_version_id: 'version-new',
          effective_from: '2026-07-25T12:00:00Z',
          state: 'current',
          revision: 1,
        }] : [],
      }] })
    }
    if (path === '/api/v1/rates/assignments/conflicts') {
      return route.fulfill({ json: { conflicts: [], requires_explicit_resolution: false } })
    }
    if (path === '/api/v1/rates/assignments/replace' && method === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>
      expect(body).toMatchObject({
        utility_account_id: 'service-1',
        rate_version_id: 'version-new',
        confirmation: 'REPLACE CURRENT',
        expected_account_revision: 1,
      })
      current = true
      return route.fulfill({ json: {
        schema_version: 'rate-assignment-result/1.0',
        assignment_id: 'assignment-new',
        electric_service_id: 'service-1',
        plan_id: 'plan-new',
        version_id: 'version-new',
        version: 2,
        effective_from: '2026-07-25T12:00:00Z',
        effective_to: null,
        state: 'current',
        effective_now: true,
        replaced_assignment_id: null,
        replaced_assignment_ids: [],
        recalculation_job_id: null,
        cost_recalculation: { queued_runs: 0, queued_run_ids: [] },
        warnings: [],
        service_revision: 2,
        history_preserved: true,
        idempotent: false,
      } })
    }
    if (path === '/api/v1/fleet/summary') {
      return route.fulfill({ json: {
        current_load_w: '1420',
        energy_today_kwh: '8.5',
        estimated_cost_today: current ? '2.64' : '0',
        reporting_devices: 1,
        active_alerts: 0,
        recent_peak_w: '2200',
        latest_heartbeat_at: '2026-07-25T12:00:00Z',
        has_live_data: true,
        has_energy_data: true,
        has_cost_data: current,
        current_rate_plan: current ? 'New Current Plan' : null,
        current_rate_price_per_kwh: current ? '0.31000000' : null,
        current_tou_bucket: current ? 'Flat rate' : null,
      } })
    }
    if (path === '/api/v1/history/query') {
      return route.fulfill({ json: {
        scope: { display_name: 'Whole Home' },
        summary: {
          energy_kwh: '8.5',
          energy_cost: current ? '2.64' : null,
          coverage_percent: '100',
          contributing_sensor_count: 1,
        },
        combined: [],
        warnings: [],
        rate_versions_used: current ? [{ rate_plan_name: 'New Current Plan' }] : [],
      } })
    }
    if (path === '/api/v1/alerts') return route.fulfill({ json: [] })
    if (path === '/api/v1/admin/utility-bill-imports') return route.fulfill({ json: [] })
    return route.fulfill({ json: [] })
  })

  await page.goto('/billing?advanced=rates&tab=versions')
  await expect(page.getByRole('tab', { name: 'Versions' })).toHaveAttribute('aria-selected', 'true')
  const versionRow = page.getByRole('listitem').filter({ hasText: 'New Current Plan' })
  await versionRow.getByRole('button', { name: 'Make current' }).click()
  const assignment = page.getByRole('region', { name: 'Make rate version current' })
  await assignment.getByRole('button', { name: 'Make current' }).click()

  await expect(page.getByText('New Current Plan', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'Ready' }).first()).toBeVisible()
  await page.getByRole('link', { name: 'Home', exact: true }).click()
  await expect(page.getByText('New Current Plan', { exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'History', exact: true }).click()
  await expect(page.getByText(/Calculated using historically effective plan: New Current Plan/)).toBeVisible()
  expect(documentRequests).toBe(1)
})
