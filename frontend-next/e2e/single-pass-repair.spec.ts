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

const service = {
  id: 'service-1',
  site_id: home.id,
  nickname: 'Home electric service',
  utility_name: 'SCE',
  generation_provider: 'sce',
  timezone: home.timezone,
  currency: home.currency,
  billing_cycle_start_day: 1,
  cost_scope: 'energy_only',
  status: 'active',
  revision: 1,
  readiness: { rate: 'ready', cost: 'ready', topology_complete: true },
  rate_context: {
    current_plan: 'TOU-D 4 PM to 9 PM',
    current_version: 2,
    current_period: 'Off-Peak',
    current_price_per_kwh: '0.34400000',
    next_period: 'On-Peak',
    next_price_per_kwh: '0.56700000',
    billing_cycle: { starts_at: '2026-07-01T07:00:00Z', ends_at: '2026-08-01T07:00:00Z' },
  },
}

const bill = {
  id: 'bill-1',
  utility_account_id: service.id,
  status: 'extracted',
  extraction_method: 'text',
  created_at: '2026-07-24T23:37:00Z',
  page_count: 6,
  revision: 1,
  cycle_draft: {
    id: 'cycle-draft-1',
    total_usage_kwh: '951.000',
    full_bill_total: '355.00',
    starts_at: '2026-06-01T07:00:00Z',
    ends_at: '2026-07-01T07:00:00Z',
  },
  fields: [
    { id: 'field-1', field_key: 'plan_name', output_kind: 'rate_plan', effective_value: 'DOMESTIC', confidence: 'parser_confirmed', page_number: 1 },
    { id: 'field-2', field_key: 'total_usage_kwh', output_kind: 'billing_cycle', effective_value: '951.000', confidence: 'arithmetic_confirmed', page_number: 1 },
    { id: 'field-3', field_key: 'tier_1_rate', output_kind: 'rate_plan', effective_value: '0.30000000', confidence: 'arithmetic_confirmed', page_number: 3 },
  ],
  conflicts: [],
  blocking_warnings: [],
  normalized_artifact: {
    schema_version: 'normalized-utility-bill/1.0',
    parser_id: 'sce_residential_bill_v1',
    parser_version: '1.0.0',
    artifact: {
      artifact_id: 'artifact-1',
      display_filename: 'sanitized-sce-domestic-bill.pdf',
      sha256: 'a'.repeat(64),
      mime_type: 'application/pdf',
      byte_size: 4264,
      page_count: 6,
      extraction_method: 'text',
      imported_at: '2026-07-24T23:37:00Z',
    },
    utility: {
      name: 'Southern California Edison',
      document_type: 'residential_electric_bill',
      rate_plan_code: 'DOMESTIC',
    },
    billing_cycle: {
      total_usage_kwh: '951.000',
      full_bill_total: '355.00',
      starts_at: '2026-06-01T07:00:00Z',
      ends_at: '2026-07-01T07:00:00Z',
    },
    plan_candidate: {
      plan_name: 'DOMESTIC',
      plan_code: 'DOMESTIC',
      threshold_interpretation: 'fixed_cycle_threshold',
    },
    line_items: [],
    evidence: [
      { field: 'plan_name', output_kind: 'rate_plan', value: 'DOMESTIC', confidence: 'parser_confirmed', source_page: 1, parser_version: '1.0.0' },
      { field: 'total_usage_kwh', output_kind: 'billing_cycle', value: '951.000', confidence: 'arithmetic_confirmed', source_page: 1, parser_version: '1.0.0' },
    ],
    validation: { status: 'pass' },
    warnings: [],
    missing_fields: [
      { field: 'account_suffix', output_kind: 'account', value: null, state: 'not_found_on_bill', required: false, reason: 'The uploaded detail pages did not show an account number.' },
    ],
    ignored_sections: [],
    page_classifications: [],
    processing_status: 'review_required',
  },
}

const ratePlans = {
  plans: [{
    id: 'plan-1',
    name: 'TOU-D 4 PM to 9 PM',
    code: 'TOU-D-4-9PM',
    status: 'active',
    lifecycle_revision: 2,
    versions: [{ id: 'version-1', version: 2, status: 'active', pricing_model: 'time_of_use' }],
  }],
}

interface MockOptions {
  failFirstBillUpload?: boolean
  billingOnly?: boolean
}

interface ObservedRequests {
  rateDraft?: Record<string, unknown>
  assignment?: Record<string, unknown>
  billPublished: boolean
  cycleImported: boolean
  retired: boolean
  removed: boolean
  restored: boolean
  sourceAdded: boolean
}

async function mockRepairServer(page: Page, configured = false, options: MockOptions = {}) {
  let billUploadAttempts = 0
  const hasBilling = configured || options.billingOnly === true
  const observed: ObservedRequests = {
    billPublished: false,
    cycleImported: false,
    retired: false,
    removed: false,
    restored: false,
    sourceAdded: false,
  }
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname
    if (pathname === '/api/v1/events/stream') return route.fulfill({ status: 204 })
    if (pathname === '/api/v1/admin/utility-bill-imports' && request.method() === 'POST') {
      billUploadAttempts += 1
      if (options.failFirstBillUpload && billUploadAttempts === 1) {
        return route.fulfill({
          status: 503,
          contentType: 'application/problem+json',
          json: { title: 'Extractor temporarily unavailable', detail: 'Retry the reviewed upload.', status: 503 },
        })
      }
      await new Promise((resolve) => setTimeout(resolve, 250))
      return route.fulfill({ status: 201, json: bill })
    }
    if (pathname === '/api/v1/admin/utility-bill-imports' && request.method() === 'GET') {
      return route.fulfill({ json: observed.cycleImported ? [{ ...bill, status: 'imported', billing_cycle: bill.cycle_draft }] : [] })
    }
    if (pathname === `/api/v1/admin/utility-bill-imports/${bill.id}/review`) return route.fulfill({ json: { ...bill, status: 'reviewed', revision: 2 } })
    if (pathname === `/api/v1/admin/utility-bill-imports/${bill.id}/validate`) return route.fulfill({ json: { validation: { valid: true }, blocking_warnings: [] } })
    if (pathname === `/api/v1/admin/utility-bill-imports/${bill.id}/publish-and-assign`) {
      observed.billPublished = true
      return route.fulfill({ json: { status: 'active' } })
    }
    if (pathname === `/api/v1/admin/utility-bill-imports/${bill.id}/import-billing-cycle`) {
      observed.cycleImported = true
      return route.fulfill({ json: { status: 'imported' } })
    }
    if (pathname === '/api/v1/rates/plans' && request.method() === 'POST') {
      observed.rateDraft = request.postDataJSON() as Record<string, unknown>
      return route.fulfill({ status: 201, json: { plan: { id: 'draft-plan', versions: [{ id: 'draft-version', status: 'draft', version: 1 }] } } })
    }
    if (pathname === '/api/v1/rates/validate-document' && request.method() === 'POST') {
      return route.fulfill({ json: { valid: true, errors: [], warnings: [], integrity_sha256: 'a'.repeat(64) } })
    }
    if (pathname === '/api/v1/rates/preview-cost' && request.method() === 'POST') {
      return route.fulfill({ json: { energy_charge: '125.00000000', blended_energy_rate: '0.25000000', display_total: '125.00' } })
    }
    if (pathname === '/api/v1/rates/versions/draft-version/activate' && request.method() === 'POST') {
      return route.fulfill({ json: { status: 'active' } })
    }
    if (pathname === '/api/v1/rates/assignments' && request.method() === 'POST') {
      observed.assignment = request.postDataJSON() as Record<string, unknown>
      return route.fulfill({ status: 201, json: { id: 'assignment-1' } })
    }
    if (pathname === '/api/v1/admin/rate-plans/plan-1/dependencies') {
      return route.fulfill({ json: {
        dependency_token: 'b'.repeat(64),
        active_assignments: [],
        future_assignments: [],
        active_account_pointers: [],
        historical_assignment_count: 1,
        historical_calculation_count: 4,
        source_evidence_count: 2,
        bill_import_count: 1,
        permanent_draft_deletion_eligible: false,
        removal_blocked: false,
        preservation: { versions: true, source_evidence: true },
      } })
    }
    if (pathname === '/api/v1/rates/versions/version-1/retire' && request.method() === 'POST') {
      observed.retired = true
      return route.fulfill({ json: { status: 'retired' } })
    }
    if (pathname === '/api/v1/admin/rate-plans/plan-1/remove' && request.method() === 'POST') {
      observed.removed = true
      return route.fulfill({ json: { plan: { id: 'plan-1', status: 'removed' } } })
    }
    if (pathname === '/api/v1/admin/rate-plans/plan-1/restore' && request.method() === 'POST') {
      observed.restored = true
      return route.fulfill({ json: { plan: { id: 'plan-1', status: 'active' } } })
    }
    if (pathname === '/api/v1/admin/rate-sources' && request.method() === 'POST') {
      observed.sourceAdded = true
      return route.fulfill({ status: 201, json: { id: 'source-3' } })
    }
    const responses: Record<string, unknown> = {
      '/api/v1/auth/session': {
        authenticated: true,
        bootstrap_required: false,
        user: { id: 'owner-1', email: 'owner@example.test', display_name: 'Home Owner', roles: ['admin'], permissions: ['rates.view', 'rates.manage_custom', 'rates.manage_sources', 'rates.assign', 'rates.remove', 'rates.restore'], all_sites: true, site_ids: [] },
      },
      '/api/v1/sites': [home],
      '/api/v1/devices': configured ? [{
        id: 'sensor-1',
        friendly_name: 'Main panel',
        application_health: 'online_synchronized',
        current_power_w: '1420',
        last_seen_at: '2026-07-24T23:36:00Z',
        monitored_circuit: 'Whole home',
        measurement_role: 'full_account',
        ct_rating_amps: '200',
      }] : [],
      '/api/v1/utility-accounts': [service],
      [`/api/v1/utility-accounts/${service.id}/tier-status`]: {
        available: hasBilling,
        cycle: { starts_at: '2026-07-01T07:00:00Z', ends_at: '2026-08-01T07:00:00Z', days_remaining: 8 },
        current_period: 'Off-Peak',
        current_rate: '0.34400000',
        usage_kwh: '481.250',
        energy_charge: '165.55',
        projected_bill: '265.20',
        coverage_percent: '98.5',
        projection_confidence: 'high',
        tiers: [],
        warnings: [],
      },
      '/api/v1/fleet/summary': {
        current_load_w: configured ? '1420' : '0',
        energy_today_kwh: configured ? '12.450' : '0',
        estimated_cost_today: configured ? '4.28' : '0',
        billing_cycle_energy_kwh: configured ? '481.250' : '0',
        billing_cycle_estimated_cost: configured ? '165.55' : '0',
        projected_bill: configured ? '265.20' : hasBilling ? '0.00' : null,
        reporting_devices: configured ? 1 : 0,
        total_devices: configured ? 1 : 0,
        online_devices: configured ? 1 : 0,
        active_alerts: 0,
        recent_peak_w: configured ? '3850' : '0',
        latest_data_at: configured ? '2026-07-24T23:36:00Z' : null,
        has_live_data: configured,
        has_energy_data: configured,
        has_cost_data: configured,
        current_rate_plan: hasBilling ? 'TOU-D 4 PM to 9 PM' : null,
        current_rate: hasBilling ? '0.34400000' : null,
        current_rate_period: hasBilling ? 'Off-Peak' : null,
        next_rate_period: hasBilling ? 'On-Peak' : null,
        next_rate: hasBilling ? '0.56700000' : null,
      },
      '/api/v1/alerts': [],
      '/api/v1/admin/utility-bill-imports': [],
      '/api/v1/rates/plans': ratePlans,
      '/api/v1/admin/rate-sources': {
        sources: [
          { id: 'source-1', name: 'SCE Residential TOU Page', url: 'https://www.sce.com/rates', parser_id: 'sce_public_tou_html_v1', enabled: true, last_success_at: '2026-07-24T20:00:00Z' },
          { id: 'source-2', name: 'Private utility bills', url: 'urn:power-monitor:utility-bill:service-1', parser_id: 'utility_bill_pdf_v1', enabled: false },
        ],
      },
      '/api/v1/history/query': {
        scope: { display_name: 'Whole Home' },
        summary: { energy_kwh: configured ? '12.450' : null, energy_cost: configured ? '4.28' : null, coverage_percent: configured ? '98.5' : '0', contributing_sensor_count: configured ? 1 : 0 },
        combined: [],
        warnings: [],
        rate_versions_used: [],
      },
    }
    const response = responses[pathname]
    if (response !== undefined) return route.fulfill({ json: response })
    return route.fulfill({ json: [] })
  })
  return observed
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('pm-single-home-onboarding-complete', 'true')
  })
})

test('bill importer is visible, keyboard contained, retryable, and URL-backed', async ({ page }) => {
  await mockRepairServer(page)
  await page.goto('/billing')
  await expect(page).toHaveScreenshot('billing-simple.png', { fullPage: true, animations: 'disabled' })
  const trigger = page.getByRole('button', { name: 'Upload electric bill' }).first()
  await trigger.click()
  await expect(page).toHaveURL(/action=upload/)
  const dialog = page.getByRole('dialog', { name: 'Upload electric bill' })
  await expect(dialog).toBeVisible()
  await expect(page.getByText('Choose your electric bill')).toBeVisible()
  await expect(page.locator('.modal-layer-backdrop')).toBeVisible()
  await expect(page.locator('body')).toHaveCSS('overflow', 'hidden')
  const geometry = await dialog.evaluate((element) => {
    const dialogBox = element.getBoundingClientRect()
    const backdrop = document.querySelector<HTMLElement>('.modal-layer-backdrop')
    const content = element.closest<HTMLElement>('.modal-layer-content')
    return {
      width: dialogBox.width,
      height: dialogBox.height,
      dialogZ: Number(getComputedStyle(content as Element).zIndex),
      backdropZ: Number(getComputedStyle(backdrop as Element).zIndex),
    }
  })
  expect(geometry.width).toBeGreaterThan(300)
  expect(geometry.height).toBeGreaterThan(300)
  expect(geometry.dialogZ).toBeGreaterThan(geometry.backdropZ)
  await expect(dialog.getByRole('button', { name: 'Close bill import' })).toBeFocused()
  await expect(page).toHaveScreenshot('importer-upload.png', { fullPage: true, animations: 'disabled' })

  await page.locator('input[type="file"]').setInputFiles(path.resolve('../backend/tests/fixtures/bills/sanitized-sce-domestic-bill.pdf'))
  await page.getByRole('button', { name: 'Upload and review' }).click()
  await expect(page.getByText('Plan name')).toBeVisible()
  await expect(dialog.getByText('sanitized-sce-domestic-bill.pdf')).toBeVisible()
  await expect(dialog.getByText('Southern California Edison')).toBeVisible()
  await expect(dialog.getByText('6 pages · Text extraction', { exact: false })).toBeVisible()
  await expect(dialog.getByText('Fields not found on this bill (1)')).toBeVisible()
  await expect(dialog.getByText('Unknown', { exact: true })).toHaveCount(0)
  await expect(page).toHaveScreenshot('importer-review.png', { fullPage: true, animations: 'disabled' })
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(page).toHaveURL(/\/billing$/)
  await expect(trigger).toBeFocused()

  await trigger.click()
  await page.reload()
  await expect(dialog).toBeVisible()
  await page.goBack()
  await expect(dialog).toHaveCount(0)
  await expect(page.locator('body')).not.toHaveCSS('overflow', 'hidden')

  await page.goto('/bill-import')
  await expect(page).toHaveURL(/\/billing\?action=upload/)
  await expect(dialog).toBeVisible()
})

test('Billing rate-plan menus close predictably before lifecycle confirmation', async ({ page }) => {
  await mockRepairServer(page, true)
  await page.goto('/billing')
  const trigger = page.getByRole('button', { name: 'Rate plan actions' })

  await trigger.scrollIntoViewIfNeeded()
  await trigger.click()
  await expect(page.getByRole('menuitem', { name: 'Remove plan' })).toBeVisible()
  await page.getByRole('heading', { name: 'Billing', exact: true }).click()
  await expect(page.getByRole('menuitem', { name: 'Remove plan' })).toHaveCount(0)

  await trigger.click()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('menuitem', { name: 'Remove plan' })).toHaveCount(0)
  await expect(trigger).toBeFocused()

  await trigger.click()
  await page.getByRole('menuitem', { name: 'Remove plan' }).click()
  await expect(page.getByRole('menu')).toHaveCount(0)
  await expect(page.getByRole('dialog', { name: 'Remove rate plan' })).toBeVisible()
  await page.getByRole('button', { name: 'Cancel' }).click()

  await trigger.click()
  await page.getByRole('menuitem', { name: 'Remove from Electric Service' }).click()
  await expect(page.getByRole('menu')).toHaveCount(0)
  await expect(page.getByRole('dialog', { name: 'Remove plan from Electric Service' })).toBeVisible()
})

test('reviewed bill applies its plan and cycle and refreshes Billing', async ({ page }) => {
  const observed = await mockRepairServer(page)
  await page.goto('/billing?action=upload')
  const dialog = page.getByRole('dialog', { name: 'Upload electric bill' })
  await page.locator('input[type="file"]').setInputFiles(path.resolve('../backend/tests/fixtures/bills/sanitized-sce-domestic-bill.pdf'))
  await page.getByRole('button', { name: 'Upload and review' }).click()
  await page.getByRole('button', { name: 'Confirm extracted values' }).click()
  await expect(page.getByRole('heading', { name: 'Confirm the reviewed values' })).toBeVisible()
  await page.getByRole('checkbox', { name: 'I reviewed these values and want to continue.' }).check()
  await page.getByRole('button', { name: 'Continue to Apply' }).click()
  await page.getByRole('button', { name: 'Apply plan and billing cycle' }).click()
  await expect(page.getByRole('heading', { name: 'Bill applied' })).toBeVisible()
  expect(observed.billPublished).toBe(true)
  expect(observed.cycleImported).toBe(true)
  await page.getByRole('button', { name: 'Return to Billing' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByRole('table').locator('tbody').getByText('Imported', { exact: true })).toBeVisible()
})

test('advanced rate editor restores every structured section without raw source noise', async ({ page }) => {
  const observed = await mockRepairServer(page)
  await page.goto('/billing?advanced=rates')
  await page.getByRole('button', { name: 'New plan' }).click()
  const editor = page.locator('.rate-editor-shell')
  await expect(editor.getByRole('tab')).toHaveCount(10)
  await expect(editor.getByText('Plan details', { exact: true })).toBeVisible()
  await expect(page).toHaveScreenshot('rate-editor-details.png', { fullPage: true, animations: 'disabled' })
  await editor.getByRole('tab', { name: '5 TOU schedules' }).click()
  await expect(editor.getByText('Time-of-use schedules', { exact: true })).toBeVisible()
  await expect(page).toHaveScreenshot('rate-editor-schedules.png', { fullPage: true, animations: 'disabled' })
  await editor.getByRole('tab', { name: '10 Publish' }).click()
  await expect(editor.getByText('Save, publish, and assign')).toBeVisible()
  await expect(page).toHaveScreenshot('rate-editor-lifecycle.png', { fullPage: true, animations: 'disabled' })
  const lifecycle = editor.locator('.lifecycle-steps')
  await lifecycle.getByRole('button', { name: 'Save draft' }).click()
  await lifecycle.getByRole('button', { name: 'Validate' }).click()
  await lifecycle.getByRole('button', { name: 'Publish version' }).click()
  await lifecycle.getByRole('button', { name: 'Assign plan' }).click()
  await expect.poll(() => observed.assignment).toBeTruthy()
  expect(observed.rateDraft?.schema_version).toBe('power-monitor-rate-plan/1.0')
  expect((observed.rateDraft?.seasons as Array<{ schedules: Array<{ periods: Array<{ price_per_kwh: unknown }> }> }>)[0]?.schedules[0]?.periods[0]?.price_per_kwh).toBe('0.25000000')
  expect(observed.assignment?.rate_version_id).toBe('draft-version')
  await editor.getByRole('button', { name: 'Close editor' }).click()

  await page.getByRole('button', { name: 'Lifecycle' }).click()
  const lifecyclePanel = page.getByRole('region', { name: 'Lifecycle controls for TOU-D 4 PM to 9 PM' })
  await expect(lifecyclePanel).toBeVisible()
  await lifecyclePanel.getByRole('button', { name: 'Retire version' }).click()
  await expect.poll(() => observed.retired).toBe(true)
  await page.getByRole('button', { name: 'Lifecycle' }).click()
  await page.getByLabel('Type TOU-D-4-9PM to confirm removal').fill('TOU-D-4-9PM')
  await page.getByRole('button', { name: 'Remove plan' }).click()
  await expect.poll(() => observed.removed).toBe(true)

  await page.getByRole('tab', { name: 'Sources', exact: true }).click()
  await expect(page.getByText('sce.com · Official source')).toBeVisible()
  await expect(page.getByText('urn:power-monitor:utility-bill:service-1')).toBeHidden()
  await expect(page).toHaveScreenshot('rate-editor-sources.png', { fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: 'Add source' }).click()
  await page.getByLabel('Name').fill('SCE official tariff')
  await page.getByLabel('Approved HTTPS URL').fill('https://www.sce.com/rates/tariffs')
  await page.getByRole('button', { name: 'Add approved source' }).click()
  await expect.poll(() => observed.sourceAdded).toBe(true)

  await page.getByRole('tab', { name: 'Removed', exact: true }).click()
  await page.getByRole('button', { name: 'Restore' }).first().click()
  await expect.poll(() => observed.restored).toBe(true)
})

test('bill importer exposes a recoverable error state and retries the same file', async ({ page }) => {
  await mockRepairServer(page, false, { failFirstBillUpload: true })
  await page.goto('/billing?action=upload')
  await page.locator('input[type="file"]').setInputFiles(path.resolve('../backend/tests/fixtures/bills/sanitized-sce-domestic-bill.pdf'))
  await page.getByRole('button', { name: 'Upload and review' }).click()
  await expect(page.getByRole('alert')).toContainText('Retry the reviewed upload.')
  await expect(page).toHaveScreenshot('importer-error.png', { fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: 'Retry this step' }).click()
  await expect(page.getByText('Plan name')).toBeVisible()
})

test('Home has intentional empty and configured dashboard layouts', async ({ page }) => {
  await mockRepairServer(page)
  await page.goto('/home')
  await expect(page.getByText('Connect your first sensor')).toBeVisible()
  await expect(page.locator('.home-onboarding-grid')).toBeVisible()
  await expect(page).toHaveScreenshot('home-repair-empty.png', { fullPage: true, animations: 'disabled' })
  await page.unroute('**/api/v1/**')
  await mockRepairServer(page, false, { billingOnly: true })
  await page.reload()
  await expect(page.locator('[data-metric-identity="home.current_plan"]')).toContainText('TOU-D 4 PM to 9 PM')
  await expect(page.locator('[data-metric-identity="home.live_status"]')).toContainText('Not connected')
  await expect(page.getByText('Billing setup complete')).toBeVisible()
  await expect(page).toHaveScreenshot('home-repair-billing-no-live.png', { fullPage: true, animations: 'disabled' })
  await page.unroute('**/api/v1/**')
  await mockRepairServer(page, true)
  await page.reload()
  await expect(page.getByText('Live power')).toBeVisible()
  await expect(page.getByText('Billing snapshot')).toBeVisible()
  await expect(page).toHaveScreenshot('home-repair-connected.png', { fullPage: true, animations: 'disabled' })
})

test('History preserves intentional no-data and configured layouts', async ({ page }) => {
  await mockRepairServer(page)
  await page.goto('/history')
  await expect(page.getByRole('heading', { name: 'History' })).toBeVisible()
  await expect(page).toHaveScreenshot('history-no-data.png', { fullPage: true, animations: 'disabled' })
  await page.unroute('**/api/v1/**')
  await mockRepairServer(page, true)
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Whole Home', exact: true })).toBeVisible()
  await expect(page).toHaveScreenshot('history-data.png', { fullPage: true, animations: 'disabled' })
})

test('repair surfaces do not overflow or overlap at the active viewport', async ({ page }) => {
  await mockRepairServer(page, true)
  await page.goto('/home')
  await expectNoDocumentOverflow(page)
  await page.goto('/billing?advanced=rates')
  await page.getByRole('button', { name: 'New plan' }).click()
  await expectNoDocumentOverflow(page)
  await page.getByRole('button', { name: 'Upload electric bill' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expectNoDocumentOverflow(page)
})

async function expectNoDocumentOverflow(page: Page) {
  const width = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    offenders: [...document.querySelectorAll<HTMLElement>('body *')].filter((element) => element.getBoundingClientRect().right > document.documentElement.clientWidth + 1).slice(0, 8).map((element) => ({ tag: element.tagName, className: element.className, right: element.getBoundingClientRect().right, width: element.getBoundingClientRect().width })),
  }))
  expect(width.overflow, JSON.stringify(width.offenders)).toBeLessThanOrEqual(1)
  const collisions = await page.locator('.home-status-grid > .metric, .rate-editor-footer > *, .workflow-footer > *').evaluateAll((elements) => {
    const visible = elements.filter((element) => (element as HTMLElement).offsetParent !== null)
    const boxes = visible.map((element) => element.getBoundingClientRect())
    return boxes.flatMap((first, firstIndex) => boxes.slice(firstIndex + 1).flatMap((second) => {
      const overlapX = Math.min(first.right, second.right) - Math.max(first.left, second.left)
      const overlapY = Math.min(first.bottom, second.bottom) - Math.max(first.top, second.top)
      return overlapX > 1 && overlapY > 1 ? [{ firstIndex, overlapX, overlapY }] : []
    }))
  })
  expect(collisions).toEqual([])
}
