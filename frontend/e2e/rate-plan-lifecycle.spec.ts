import { expect, test, type Page } from '@playwright/test'

const version = {
  id: 'version-lifecycle',
  version: 1,
  pricing_model: 'tiered',
  tier_count: 2,
  threshold_basis: 'fixed_cycle_kwh',
  effective_from: '2026-07-01',
  effective_through: null,
  status: 'active',
  source_kind: 'custom',
  source_label: 'Administrator-defined plan',
  integrity_sha256: 'a'.repeat(64),
  is_active: true,
  immutable: true,
  created_at: '2026-07-01T00:00:00Z',
}

const safePlan = {
  id: 'plan-safe',
  code: 'SAFE-LIFECYCLE',
  name: 'Safe lifecycle plan',
  description: 'Unassigned published lifecycle fixture.',
  plan_kind: 'custom',
  ownership_scope: 'global',
  currency: 'USD',
  timezone: 'America/Los_Angeles',
  status: 'active',
  lifecycle_revision: 1,
  versions: [version],
}

const blockedPlan = {
  ...safePlan,
  id: 'plan-blocked',
  code: 'BLOCKED-LIFECYCLE',
  name: 'Assigned lifecycle plan',
  description: 'Assigned dependency fixture.',
  versions: [{ ...version, id: 'version-blocked' }],
}

function dependencies(plan: typeof safePlan, blocked: boolean) {
  return {
    plan_id: plan.id,
    plan_code: plan.code,
    plan_name: plan.name,
    plan_kind: plan.plan_kind,
    origin: 'custom',
    status: plan.status,
    lifecycle_revision: plan.lifecycle_revision,
    version_count: 1,
    active_assignments: blocked
      ? [{ id: 'assignment-1', utility_account_id: 'account-1' }]
      : [],
    future_assignments: [],
    active_account_pointers: [],
    historical_assignment_count: 3,
    historical_calculation_count: 9,
    report_count: 2,
    source_evidence_count: 4,
    bill_import_count: 1,
    managed_candidate_count: 0,
    cloned_plan_count: 0,
    candidate_version_reference_count: 0,
    permanent_draft_deletion_eligible: false,
    removal_blocked: blocked,
    dependency_actions: blocked
      ? ['replace_assignment', 'schedule_replacement', 'cancel_removal']
      : [],
    preservation: {
      versions: true,
      historical_assignments: true,
      costs: true,
      reports: true,
      source_evidence: true,
      bill_imports: true,
      audit_history: true,
    },
    restore_eligible: true,
  }
}

async function mockApplication(page: Page) {
  let lifecycleStatus: 'active' | 'removed' = 'active'
  let revision = 1
  let removalReason: string | null = null

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
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
    } else if (path === '/api/v1/system/compatibility') {
      body = {
        backend_version: '1.0.0',
        backend_commit: 'e2e',
        api_schema_version: '1.0.0',
        bill_import_context_schema_version: 'utility-account-rate-context/1.0',
        protocol_version: 'pm-protocol/1.0.0',
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
        pages: ['rates'],
        breakpoints: ['desktop', 'tablet', 'mobile'],
      }
    } else if (path === '/api/v1/status-indicators/layout') {
      body = {
        registry_version: 'status-indicators/1.0',
        page: 'rates',
        breakpoint: 'desktop',
        role: 'admin',
        revision: 1,
        zones: [],
        warnings: [],
      }
    } else if (path === '/api/v1/rates/plans' && request.method() === 'GET') {
      const selected = url.searchParams.get('status')
      const currentSafe = {
        ...safePlan,
        status: lifecycleStatus,
        lifecycle_revision: revision,
        removed_at: lifecycleStatus === 'removed' ? '2026-07-24T12:00:00Z' : null,
        removed_by: lifecycleStatus === 'removed' ? 'admin-1' : null,
        removal_reason: removalReason,
      }
      body = selected === 'removed_or_retired'
        ? lifecycleStatus === 'removed' ? [currentSafe] : []
        : [
            ...(lifecycleStatus === 'active' ? [currentSafe] : []),
            blockedPlan,
          ]
    } else if (path === '/api/v1/rates/assignments') {
      body = []
    } else if (path === '/api/v1/utility-accounts') {
      body = []
    } else if (path === '/api/v1/admin/rate-candidates') {
      body = []
    } else if (path.endsWith('/dependencies')) {
      const blocked = path.includes(blockedPlan.id)
      const selected = blocked ? blockedPlan : {
        ...safePlan,
        status: lifecycleStatus,
        lifecycle_revision: revision,
      }
      body = dependencies(selected, blocked)
    } else if (path.endsWith('/remove') && request.method() === 'POST') {
      lifecycleStatus = 'removed'
      revision += 1
      removalReason = 'E2E lifecycle removal'
      body = {
        idempotent: false,
        plan: {
          ...safePlan,
          status: lifecycleStatus,
          lifecycle_revision: revision,
          removed_at: '2026-07-24T12:00:00Z',
          removed_by: 'admin-1',
          removal_reason: removalReason,
        },
        dependencies: dependencies(safePlan, false),
      }
    } else if (path.endsWith('/restore') && request.method() === 'POST') {
      lifecycleStatus = 'active'
      revision += 1
      body = {
        idempotent: false,
        plan: {
          ...safePlan,
          status: lifecycleStatus,
          lifecycle_revision: revision,
        },
        assignments_restored: false,
      }
    } else if (path === '/api/v1/fleet/summary') {
      body = { active_alerts: 0, current_load_w: '0', total_devices: 0 }
    }

    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(body),
    })
  })
}

test('removes, lists, restores, and blocks an assigned rate plan', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/billing/rate-plans')

  const safeCard = page.getByRole('heading', { name: safePlan.name }).locator('xpath=ancestor::section[1]')
  await safeCard.getByRole('button', { name: 'Remove rate plan' }).click()
  const removeDialog = page.getByRole('dialog', { name: 'Remove rate plan' })
  await expect(removeDialog.getByText('Historical calculations', { exact: true }).locator('..')).toContainText('9')
  await expect(removeDialog.getByText('Source evidence', { exact: true }).locator('..')).toContainText('4')
  await removeDialog.getByLabel('Removal reason').fill('E2E lifecycle removal')
  await removeDialog.getByLabel('Type exact plan name or code').fill(safePlan.code)
  await removeDialog.getByRole('button', { name: 'Remove rate plan' }).click()
  await expect(page.getByRole('heading', { name: safePlan.name })).toHaveCount(0)

  await page.getByRole('button', { name: 'Removed / Retired' }).click()
  const removedCard = page.getByRole('heading', { name: safePlan.name }).locator('xpath=ancestor::section[1]')
  await expect(removedCard.getByText('Future assignments disabled')).toBeVisible()
  await removedCard.getByRole('button', { name: 'Restore', exact: true }).click()
  const restoreDialog = page.getByRole('dialog', { name: 'Restore rate plan' })
  await restoreDialog.getByLabel('Restore reason').fill('Restore for E2E')
  await restoreDialog.getByRole('button', { name: 'Restore rate plan' }).click()
  await expect(page.getByRole('heading', { name: safePlan.name })).toHaveCount(0)

  await page.getByRole('button', { name: 'Active' }).click()
  const blockedCard = page.getByRole('heading', { name: blockedPlan.name }).locator('xpath=ancestor::section[1]')
  await blockedCard.getByRole('button', { name: 'Remove rate plan' }).click()
  const blockedDialog = page.getByRole('dialog', { name: 'Remove rate plan' })
  await expect(blockedDialog.getByRole('alert')).toContainText('Removal is blocked')
  await expect(blockedDialog.getByRole('button', { name: 'Resolve assignments' })).toBeVisible()
  await expect(blockedDialog.getByRole('button', { name: 'Remove rate plan' })).toHaveCount(0)
})
