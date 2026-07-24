import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'
import { emptyRateDocument } from '../src/rates'

const account = {
  id: 'account-1',
  site_id: 'site-1',
  site_name: 'Upland Site',
  utility_id: 'sce',
  utility_name: 'Southern California Edison',
  name: 'Home utility account',
  status: 'active',
  timezone: 'America/Los_Angeles',
  currency: 'USD',
  billing_cycle_start_day: 22,
  provider_mode: 'sce_bundled',
  generation_provider: 'sce',
  cost_scope: 'full_account',
  full_account_override: true,
  revision: 1,
  assignment_count: 1,
  device_count: 2,
  readiness: {
    rate: 'rate_configured_effective',
    cost: 'cost_calculation_ready',
    topology_complete: true,
  },
  rate_context: {
    state: 'rate_configured_effective',
    current_plan: 'DOMESTIC',
    current_currency: 'USD',
  },
}

const thresholdField = {
  id: 'field-threshold',
  output_kind: 'rate_plan',
  field_key: 'threshold_interpretation',
  raw_value: '579 kWh',
  normalized_value: 'unknown',
  corrected_value: null as string | null,
  effective_value: 'unknown',
  page_number: 1,
  text_region: { x0: 72, y0: 240, x1: 360, y1: 260 },
  source_excerpt: 'Tier 1 0-579 kWh',
  extraction_method: 'pdf_text',
  parser_version: 'utility-bill-parser/1.0',
  confidence: 'low',
  review_state: 'review_required',
  warnings: [{ code: 'threshold_ambiguous', message: 'Threshold basis requires review.' }],
  normalization_history: [{ operation: 'structured_numeric_bounds' }],
}

const baseBill = {
  id: 'bill-1',
  job_id: 'job-1',
  utility_account_id: account.id,
  utility_account_name: account.name,
  artifact_id: 'artifact-1',
  content_sha256: 'a'.repeat(64),
  status: 'review_required',
  source_role: 'supporting',
  extraction_method: 'pdf_text',
  parser_version: 'utility-bill-parser/1.0',
  page_count: 2,
  retention_mode: 'retain',
  original_available: true,
  rate_plan_id: 'plan-bill-1',
  rate_version_id: 'version-bill-1',
  revision: 1,
  blocking_warnings: [{
    code: 'threshold_interpretation_required',
    message: 'Confirm how the displayed threshold is calculated.',
    fields: ['threshold_interpretation'],
  }],
  extraction_warnings: [],
  created_at: '2026-07-24T08:00:00Z',
  updated_at: '2026-07-24T08:00:00Z',
  normalized: {
    account: { utility: 'Southern California Edison', account_suffix: '1234' },
    rate_plan: {
      utility: 'Southern California Edison',
      plan_name: 'DOMESTIC',
      plan_code: 'D',
      pricing_model: 'tiered',
      currency: 'USD',
      energy_charge: '322.500000000',
      tiers: [
        {
          name: 'Tier 1',
          lower_bound_kwh: '0',
          upper_bound_kwh: '579',
          usage_kwh: '579.000',
          price_per_kwh: '0.30',
          energy_charge: '173.7000000',
        },
        {
          name: 'Tier 2',
          lower_bound_kwh: '579',
          upper_bound_kwh: null,
          usage_kwh: '372.000',
          price_per_kwh: '0.40',
          energy_charge: '148.8000000',
        },
      ],
    },
    billing_cycle: {
      starts_at: '2026-07-22T07:00:00Z',
      ends_at: '2026-08-21T07:00:00Z',
      total_usage_kwh: '951.000',
      energy_subtotal: '322.500000000',
      full_bill_total: '355.000000000',
    },
  },
  fields: [thresholdField],
  conflicts: [] as Array<{
    id: string
    field_key: string
    extracted_value: string
    configured_value: string
    comparison_source: string
    status: string
    blocking: boolean
  }>,
  cycle_draft: {
    id: 'cycle-draft-1',
    status: 'draft',
    starts_at: '2026-07-22T07:00:00Z',
    ends_at: '2026-08-21T07:00:00Z',
    cycle_days: 30,
    total_usage_kwh: '951.000',
    usage_by_tier: [],
    usage_by_tou: [],
    meter_records: [],
    current_tier: 'Tier 2',
    projected_tier: 'Tier 2',
    energy_subtotal: '322.500000000',
    full_bill_total: '355.000000000',
    threshold_interpretation: 'unknown',
    reconciliation_status: 'pending',
    billing_cycle_id: undefined as string | undefined,
    utility_usage_import_id: undefined as string | undefined,
    revision: 1,
  },
}

const comparison = {
  available: true,
  calculation_correctness: 'validated_by_existing_rate_engine',
  extraction_confidence: 'administrator_confirmed',
  exact: {
    usage_kwh: '951.000',
    calculated_energy_subtotal: '322.50000000000',
    calculated_total: '322.50000000000',
    utility_energy_subtotal: '322.500000000',
    utility_full_bill_total: '355.000000000',
    energy_subtotal_difference: '0E-11',
    complete_bill_difference: '-32.50000000000',
    unexplained_difference: '32.50000000000',
  },
  display: {
    usage: '951 kWh',
    calculated_energy_subtotal: '$322.50',
    blended_energy_rate: '$0.3391/kWh',
    calculated_total: '$322.50',
    utility_energy_subtotal: '$322.50',
    utility_full_bill_total: '$355.00',
    energy_subtotal_difference: '$0.00',
    complete_bill_difference: '-$32.50',
  },
  tiers: [
    {
      tier_id: 'tier-1',
      name: 'Tier 1',
      lower_bound_kwh: '0',
      upper_bound_kwh: '579',
      display_range: '0\u2013579 kWh',
    },
    {
      tier_id: 'tier-2',
      name: 'Tier 2',
      lower_bound_kwh: '579',
      upper_bound_kwh: null,
      display_range: '580 kWh and above',
    },
  ],
  disclosure: 'Energy subtotal and complete utility bill remain separate.',
}

const importedRateDocument = {
  ...emptyRateDocument(),
  plan_name: 'DOMESTIC',
  plan_code: 'D',
  utility: 'Southern California Edison',
  description: 'Reviewed utility-bill tariff draft',
  effective_from: '2026-07-22',
  pricing_model: 'tiered' as const,
  tiers: [
    {
      tier_id: 'tier-1',
      name: 'Tier 1',
      order: 0,
      lower_bound_inclusive_kwh: '0',
      upper_bound_exclusive_kwh: '579',
      lower_bound_multiplier: null,
      upper_bound_multiplier: null,
      price_per_kwh: '0.30000000',
      tou_prices: {},
      season: null,
      source_citation: 'utility-bill:bill-1',
    },
    {
      tier_id: 'tier-2',
      name: 'Tier 2',
      order: 1,
      lower_bound_inclusive_kwh: '579',
      upper_bound_exclusive_kwh: null,
      lower_bound_multiplier: null,
      upper_bound_multiplier: null,
      price_per_kwh: '0.40000000',
      tou_prices: {},
      season: null,
      source_citation: 'utility-bill:bill-1',
    },
  ],
  source_label: 'Reviewed utility bill bill-1',
  source_note: 'Sanitized evidence artifact artifact-1',
}

async function mockApplication(page: Page, initialBill = baseBill) {
  let bill = structuredClone(initialBill)
  const requests: string[] = []

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const apiPath = url.pathname
    requests.push(`${request.method()} ${apiPath}`)
    let body: unknown = {}

    if (apiPath === '/api/v1/auth/session') {
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
    } else if (apiPath === '/api/v1/public/interface-text' || apiPath === '/api/v1/interface-text') {
      body = { revision: 0, values: {} }
    } else if (apiPath === '/api/v1/sites') {
      body = [{
        id: 'site-1',
        name: 'Upland Site',
        timezone: 'America/Los_Angeles',
        allowed_cidrs: [],
        allowed_domains: [],
        allow_public_polling: false,
      }]
    } else if (apiPath === '/api/v1/status-indicators/registry') {
      body = {
        registry_version: 'status-indicators/1.0',
        indicators: [],
        zones: [],
        pages: ['rates', 'costs'],
        breakpoints: ['desktop', 'tablet', 'mobile'],
      }
    } else if (apiPath === '/api/v1/status-indicators/layout') {
      body = {
        registry_version: 'status-indicators/1.0',
        page: 'rates',
        breakpoint: 'desktop',
        role: 'admin',
        revision: 1,
        zones: [],
        warnings: [],
      }
    } else if (apiPath === '/api/v1/utility-accounts') {
      body = [account]
    } else if (apiPath.startsWith('/api/v1/rates/versions/')) {
      body = {
        version: {
          id: apiPath.split('/').at(-1),
          version: 1,
          status: 'draft',
          effective_from: importedRateDocument.effective_from,
        },
        document: importedRateDocument,
      }
    } else if (apiPath === '/api/v1/rates/plans' && request.method() === 'POST') {
      body = {
        plan: {
          id: 'custom-plan-1',
          versions: [{
            id: 'custom-version-1',
            version: 1,
            status: 'draft',
            effective_from: importedRateDocument.effective_from,
          }],
        },
      }
    } else if (
      request.method() === 'POST'
      && apiPath === `/api/v1/admin/utility-accounts/${account.id}/bill-imports`
    ) {
      body = bill
    } else if (apiPath === '/api/v1/admin/utility-bill-imports' && request.method() === 'GET') {
      body = []
    } else if (apiPath.endsWith('/review') && request.method() === 'PUT') {
      bill = {
        ...bill,
        status: 'ready_to_publish',
        revision: bill.revision + 1,
        blocking_warnings: [],
        source_role: 'authoritative_account_specific',
        fields: [{
          ...thresholdField,
          corrected_value: 'fixed_cycle_threshold',
          effective_value: 'fixed_cycle_threshold',
          confidence: 'administrator_confirmed',
          review_state: 'corrected',
        }],
        cycle_draft: {
          ...bill.cycle_draft,
          threshold_interpretation: 'fixed_cycle_threshold',
        },
      }
      body = bill
    } else if (apiPath === `/api/v1/admin/utility-bill-imports/${bill.id}`) {
      body = bill
    } else if (apiPath.endsWith('/evidence/pages/1')) {
      body = {
        bill_import_id: bill.id,
        artifact_id: bill.artifact_id,
        page_number: 1,
        parser_version: bill.parser_version,
        fields: [{
          field_key: thresholdField.field_key,
          source_excerpt: thresholdField.source_excerpt,
          text_region: thresholdField.text_region,
          method: thresholdField.extraction_method,
          confidence: thresholdField.confidence,
        }],
      }
    } else if (apiPath.endsWith('/comparison')) {
      body = comparison
    } else if (apiPath.endsWith('/validate') && request.method() === 'POST') {
      body = {
        bill_status: bill.status,
        blocking_warnings: bill.blocking_warnings,
        validation: { valid: true, errors: [], warnings: [] },
      }
    } else if (apiPath.endsWith('/import-billing-cycle') && request.method() === 'POST') {
      bill = {
        ...bill,
        revision: bill.revision + 1,
        cycle_draft: {
          ...bill.cycle_draft,
          status: 'imported',
          billing_cycle_id: 'cycle-1',
          utility_usage_import_id: 'usage-import-1',
        },
      }
      body = { status: 'imported' }
    } else if (apiPath.endsWith('/original') && request.method() === 'DELETE') {
      bill = { ...bill, original_available: false, revision: bill.revision + 1 }
      body = { original_available: false }
    } else if (apiPath === '/api/v1/fleet/summary') {
      body = { active_alerts: 0, current_load_w: '0', total_devices: 0 }
    }

    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
  })

  return requests
}

test('reviews separate bill outputs and selectively merges them into the existing Custom Plan draft', async ({ page }) => {
  const requests = await mockApplication(page)
  page.on('dialog', (dialog) => dialog.accept())

  await page.goto('/rates/new')
  await page.getByLabel('Plan name').fill('My preserved custom draft')
  await page.getByLabel('Plan code').fill('KEEP-ME')
  await page.getByRole('button', { name: 'Import utility bill' }).click()
  await expect(page.getByRole('heading', { name: 'Import utility bill' })).toBeVisible()
  await page.getByLabel('Utility bill import', { exact: true }).getByRole('button', { name: 'Next' }).click()

  const fixture = path.resolve(
    process.cwd(),
    '../backend/tests/fixtures/bills/text-tiered-bill.pdf',
  )
  await page.getByLabel('Utility-bill PDF').setInputFiles(fixture)
  await page.getByRole('button', { name: 'Upload and create drafts' }).click()
  await expect(page.getByText(/separate rate-plan and billing-cycle drafts/i)).toBeVisible()
  await expect(page.getByText('Automatic activation').locator('..')).toContainText('Disabled')
  await expect(page.getByText('Tier 1 0-579 kWh')).toBeVisible()

  await page.getByRole('button', { name: /Rate rules/ }).click()
  await expect(page.getByRole('heading', { name: 'Tier preview' })).toBeVisible()
  await expect(page.getByRole('cell', { name: '0\u2013579 kWh' })).toBeVisible()
  await expect(page.getByRole('cell', { name: '580 kWh and above' })).toBeVisible()
  await expect(page.getByRole('cell', { name: '$173.70' })).toBeVisible()
  await expect(page.getByRole('cell', { name: '$148.80' })).toBeVisible()
  await page.screenshot({
    path: '../docs/screenshots/utility-bill-import-tier-preview.png',
    fullPage: true,
  })
  await page.getByLabel('Administrator decision').selectOption('correct')
  await page.getByLabel('Corrected exact value').fill('fixed_cycle_threshold')

  await page.getByRole('button', { name: /Calculation preview/ }).click()
  await expect(page.getByText('$322.50', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('$0.3391/kWh', { exact: true })).toBeVisible()
  await expect(page.getByText('$355.00', { exact: true })).toBeVisible()
  await page.getByText('Exact unrounded comparison values').click()
  await expect(page.getByText(/322\.50000000000/)).toBeVisible()

  await page.getByRole('button', { name: /Review outputs/ }).click()
  await page.getByText('Rate-plan extraction').click()
  await expect(page.getByText(/173\.7000000/)).toBeVisible()
  await page.getByRole('button', { name: 'Save reviewed fields and outputs' }).click()
  await expect(page.getByText(/ready for rate-engine validation/i)).toBeVisible()

  await page.getByRole('button', { name: 'Delete original now' }).click()
  await expect(page.getByText(/original PDF was removed/i)).toBeVisible()
  await expect(page.getByText(/Sanitized evidence, normalized values, and audit history remain/i)).toBeVisible()

  await page.getByRole('button', { name: /Apply to custom draft/ }).click()
  await page.getByRole('button', { name: 'Validate reviewed draft' }).click()
  await expect(page.getByText('Rate-engine validation passed')).toBeVisible()
  await page.getByRole('button', { name: 'Import reviewed billing cycle' }).click()
  await expect(page.getByText(/without overwriting monitored readings/i)).toBeVisible()
  await page.getByLabel('Plan code choice').selectOption('import')
  await page.getByLabel('Complete tariff rules choice').selectOption('import')
  await page.getByLabel('Source evidence choice').selectOption('import')
  await page.getByRole('button', { name: 'Apply selected values to Custom Plan' }).click()

  await expect(page.getByText(/applied to this unsaved Custom Plan draft/i)).toBeVisible()
  await expect(page.getByLabel('Plan name')).toHaveValue('My preserved custom draft')
  await expect(page.getByLabel('Plan code')).toHaveValue('D')
  const saveRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/rates/plans') && request.method() === 'POST')
  await page.getByRole('button', { name: 'Save draft' }).click()
  const savedDocument = JSON.parse((await saveRequest).postData() ?? '{}') as { plan_name: string; plan_code: string; pricing_model: string; tiers: unknown[]; source_label: string }
  expect(savedDocument.plan_name).toBe('My preserved custom draft')
  expect(savedDocument.plan_code).toBe('D')
  expect(savedDocument.pricing_model).toBe('tiered')
  expect(savedDocument.tiers).toHaveLength(2)
  expect(savedDocument.source_label).toContain('Reviewed utility bill')

  expect(requests).toContain('POST /api/v1/admin/utility-accounts/account-1/bill-imports')
  expect(requests).toContain('PUT /api/v1/admin/utility-bill-imports/bill-1/review')
  expect(requests).toContain('POST /api/v1/admin/utility-bill-imports/bill-1/import-billing-cycle')
  expect(requests).toContain('DELETE /api/v1/admin/utility-bill-imports/bill-1/original')
  expect(requests).not.toContain('POST /api/v1/admin/utility-bill-imports/bill-1/publish-and-assign')
})

test('keeps an unresolved official-source conflict visible and blocks publication', async ({ page }) => {
  const conflictingBill = {
    ...baseBill,
    blocking_warnings: [
      ...baseBill.blocking_warnings,
      {
        code: 'source_conflict',
        message: 'Uploaded tier price conflicts with the active official source.',
        fields: ['tiers'],
      },
    ],
    conflicts: [{
      id: 'conflict-1',
      field_key: 'tiers',
      extracted_value: '$0.40/kWh',
      configured_value: '$0.38/kWh',
      comparison_source: 'approved_official_source',
      status: 'unresolved',
      blocking: true,
    }],
  }
  await mockApplication(page, conflictingBill)
  await page.setViewportSize({ width: 390, height: 844 })

  await page.goto('/rates/import-bill?account_id=account-1')
  await expect(page).toHaveURL(/\/rates\/new\?.*bill_import=open/)
  await expect(page.getByRole('heading', { name: 'Import utility bill' })).toBeVisible()
  await page.getByLabel('Utility bill import', { exact: true }).getByRole('button', { name: 'Next' }).click()
  const fixture = path.resolve(
    process.cwd(),
    '../backend/tests/fixtures/bills/text-tiered-bill.pdf',
  )
  await page.getByLabel('Utility-bill PDF').setInputFiles(fixture)
  await page.getByRole('button', { name: 'Upload and create drafts' }).click()

  await page.getByRole('button', { name: /Confidence & conflicts/ }).click()
  await expect(page.getByText('approved official source')).toBeVisible()
  await expect(page.getByText('$0.40/kWh')).toBeVisible()
  await expect(page.getByText('$0.38/kWh')).toBeVisible()

  await page.getByRole('button', { name: /Apply to custom draft/ }).click()
  await page.getByRole('button', { name: 'Validate reviewed draft' }).click()
  await expect(page.getByRole('button', { name: 'Apply selected values to Custom Plan' })).toBeDisabled()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(1)
})

test('shows actionable importer empty states instead of a blank workspace', async ({ page }) => {
  await mockApplication(page)
  await page.route('**/api/v1/utility-accounts', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: '[]' })
  })
  await page.goto('/rates/new?bill_import=open')
  await expect(page.getByRole('heading', { name: 'Import utility bill' })).toBeVisible()
  await expect(page.getByText('No utility account')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Create utility account' })).toBeVisible()
})

test('a failed editor chunk renders a recoverable error rather than a blank page', async ({ page }) => {
  await mockApplication(page)
  await page.route('**/assets/RateEditorPage-*.js', async (route) => {
    await route.abort('failed')
  })
  await page.goto('/rates/new?bill_import=open')
  await expect(page.getByRole('alert')).toContainText('Something needs attention')
  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible()
})
