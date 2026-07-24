import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BillImportWorkspace } from '../src/pages/BillImportPage'
import { emptyRateDocument } from '../src/rates'

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

const context = {
  schema_version: 'utility-account-rate-context/1.0',
  api_version: '1.0.0',
  backend_version: '1.0.0',
  backend_commit: null,
  generated_client_schema_version: 'utility-account-rate-context/1.0',
  account_id: null,
  site_id: null,
  account: null,
  available_accounts: [],
  current_plan: null,
  current_assignment: null,
  current_rate_version: null,
  current_period: null,
  readiness: {
    account_configured: false,
    rate_assigned: false,
    rate_effective: false,
  },
}

const strictBill = {
  id: 'bill-1',
  job_id: 'job-1',
  utility_account_id: null,
  utility_account_name: 'Not assigned yet',
  artifact_id: 'artifact-1',
  content_sha256: 'a'.repeat(64),
  status: 'review_required',
  source_role: 'supporting',
  extraction_method: 'text',
  parser_id: 'sce_residential_bill_v1',
  parser_version: '1.0.0',
  page_count: 6,
  retention_mode: 'retain',
  original_available: true,
  rate_plan_id: null,
  rate_version_id: null,
  revision: 1,
  blocking_warnings: [{
    code: 'single_bill_incomplete_tariff',
    message: 'A single bill does not prove all reusable tariff rules.',
  }],
  extraction_warnings: [],
  created_at: '2026-07-24T12:00:00Z',
  updated_at: '2026-07-24T12:00:00Z',
  normalized: {
    account: { utility: 'Southern California Edison' },
    rate_plan: {
      plan_name: 'DOMESTIC',
      plan_code: 'DOMESTIC',
      utility: 'Southern California Edison',
      pricing_model: 'tiered',
      tiers: [
        {
          name: 'Tier 1',
          lower_bound_kwh: '0',
          upper_bound_kwh: '579.0',
          usage_kwh: '579',
          price_per_kwh: '0.30863',
          energy_charge: null,
        },
        {
          name: 'Tier 2',
          lower_bound_kwh: '579.0',
          upper_bound_kwh: null,
          usage_kwh: '372',
          price_per_kwh: '0.40962',
          energy_charge: null,
        },
      ],
    },
    billing_cycle: {
      line_items: [{
        component: 'energy',
        section: 'delivery',
        usage_kwh: '579',
        unit_rate: '0.17862',
        amount: '103.42',
        validation: {
          exact_product: '103.42098',
          rounded_product: '103.42',
          printed_amount: '103.42',
          difference: '0.00',
          status: 'pass',
        },
      }],
    },
  },
  adapter_result: {
    schema_version: 'sce_bill_v1',
    parser_id: 'sce_residential_bill_v1',
    parser_version: '1.0.0',
    document_class: 'residential_electric_bill',
    supported_layout: 'sce_residential_multi_page_charge_details',
    automatic_publication_eligible: false,
  },
  page_classifications: [
    {
      page_number: 1,
      page_class: 'account_and_usage_summary',
      anchor_score: 0,
      matched_anchors: [],
      authoritative_for_rate_plan: false,
    },
    {
      page_number: 3,
      page_class: 'new_charge_details',
      anchor_score: 10,
      matched_anchors: ['details of your new charges'],
      authoritative_for_rate_plan: true,
    },
  ],
  ignored_sections: [{
    page_number: 2,
    page_class: 'generic_information',
    reasons: ['payment_or_balance', 'generic_definition'],
    authoritative_for_rate_plan: false,
  }],
  validation: {
    valid: true,
    automatic_publication_eligible: false,
    usage: [{
      section: 'delivery',
      tier_usage_sum_kwh: '951',
      total_usage_kwh: '951',
      status: 'pass',
    }],
    subtotal: { calculated: '353.86', printed: '353.86', status: 'pass' },
    total: { calculated: '354.15', printed: '354.15', state_tax: '0.29', status: 'pass' },
  },
  fields: [{
    id: 'field-1',
    output_kind: 'rate_plan',
    field_key: 'daily_baseline_formula',
    raw_value: null,
    normalized_value: null,
    corrected_value: null,
    effective_value: null,
    page_number: null,
    text_region: null,
    source_excerpt: null,
    extraction_method: 'text',
    parser_version: '1.0.0',
    parser_rule: 'sce.missing_rule.daily_baseline_formula.v1',
    validation_result: null,
    confidence: 'missing',
    review_state: 'needs_review',
    warnings: [{
      code: 'field_not_found',
      message: 'This bill proves a cycle allowance, not the reusable daily formula.',
    }],
    normalization_history: [],
  }],
  conflicts: [],
}

describe('strict SCE bill review interface', () => {
  it('shows parser identity, page boundaries, exact rates, arithmetic, and explained nulls', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.href
          : input.url
      if (url.includes('/utility-bill-import-context')) return response(context)
      if (url.endsWith('/utility-bill-imports/bill-1')) return response(strictBill)
      if (url.endsWith('/utility-bill-imports/bill-1/comparison')) {
        return response({ available: false, reason: 'Administrator review is required.' })
      }
      if (url.includes('/evidence/pages/')) {
        return response({
          bill_import_id: 'bill-1',
          artifact_id: 'artifact-1',
          page_number: 1,
          parser_version: '1.0.0',
          fields: [],
        })
      }
      if (url.includes('/utility-bill-imports')) return response([])
      return response({})
    }))
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    render(
      <MemoryRouter initialEntries={['/billing/rate-plans/new?bill_id=bill-1']}>
        <QueryClientProvider client={client}>
          <BillImportWorkspace
            currentDraft={emptyRateDocument()}
            editorMode="new_custom_plan"
            onApplyDraft={vi.fn()}
            onClose={vi.fn()}
          />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByText('sce_residential_bill_v1')).toBeVisible()
    expect(screen.getByText('Authoritative charge detail')).toBeVisible()
    expect(screen.getByText(/payments, definitions, notices/i)).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: /Rate rules/ }))
    expect(await screen.findByText('$0.30863/kWh')).toBeVisible()
    expect(screen.getByText('$0.40962/kWh')).toBeVisible()
    expect(screen.getByText('Exact Decimal pass')).toBeVisible()
    expect(screen.getAllByText('Not found on this bill')).toHaveLength(2)
    expect(screen.getByText(/cycle allowance, not the reusable daily formula/i)).toBeVisible()
    expect(screen.getByText(/rounded explanatory material/i)).toBeVisible()
  })
})
