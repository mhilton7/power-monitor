import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

const requestMock = vi.hoisted(() => vi.fn())

vi.mock('../src/api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../src/api/client')>()
  return {
    ...original,
    request: requestMock,
  }
})

import { BillImportFlow } from '../src/features/bill-import/BillImportFlow'
import type { BillImportDetail, ElectricService, Home } from '../src/types/models'

const home: Home = {
  id: 'home-1',
  name: 'Home',
  timezone: 'America/Los_Angeles',
  currency: 'USD',
  lifecycle: 'active',
  isDefault: true,
  revision: 1,
}

const service: ElectricService = {
  id: 'service-1',
  homeId: home.id,
  name: 'Home electric service',
  provider: 'Southern California Edison',
  currency: 'USD',
  timezone: home.timezone,
  billingDay: 1,
  status: 'active',
  costScope: 'energy_only',
  revision: 1,
  readiness: {
    rate: 'setup_needed',
    cost: 'setup_needed',
    topologyComplete: true,
  },
}

const bill: BillImportDetail = {
  id: 'bill-1',
  serviceId: service.id,
  status: 'review_required',
  extractionMethod: 'text',
  createdAt: '2026-07-30T12:00:00Z',
  pageCount: 2,
  usageKwh: '850',
  total: '354.15',
  startsAt: '2026-07-01T07:00:00Z',
  endsAt: '2026-08-01T07:00:00Z',
  blockingWarnings: [],
  revision: 1,
  displayFilename: 'reviewed-bill.pdf',
  utilityName: 'Southern California Edison',
  documentType: 'residential_electric_bill',
  importedAt: '2026-07-30T12:00:00Z',
  processingStatus: 'review_required',
  thresholdInterpretation: 'fixed_cycle_threshold',
  missingFields: [],
  fields: [
    {
      id: 'field-rate',
      path: 'tiers.0.price_per_kwh',
      label: 'Tier 1 price',
      outputKind: 'rate_plan',
      calculationRole: 'tariff_rule',
      value: '0.32',
      confidence: 'parser_confirmed',
      sourcePage: 2,
    },
    {
      id: 'field-usage',
      path: 'total_usage_kwh',
      label: 'Bill-reported usage',
      outputKind: 'billing_cycle',
      calculationRole: 'reference_only',
      value: '850',
      confidence: 'arithmetic_confirmed',
      sourcePage: 1,
    },
  ],
  conflicts: [],
  normalized: {
    schemaVersion: 'normalized-utility-bill/1.0',
    parserId: 'sce_residential_bill_v1',
    parserVersion: '1.1.0',
    artifact: {
      id: 'artifact-1',
      displayFilename: 'reviewed-bill.pdf',
      sha256: 'a'.repeat(64),
      mimeType: 'application/pdf',
      pageCount: 2,
      extractionMethod: 'text',
      importedAt: '2026-07-30T12:00:00Z',
    },
    utility: {
      name: 'Southern California Edison',
      documentType: 'residential_electric_bill',
      ratePlanCode: 'DOMESTIC',
    },
    billingCycle: { total_usage_kwh: '850' },
    planCandidate: { pricing_model: 'tiered' },
    lineItems: [],
    evidence: [],
    validation: { valid: true },
    warnings: [],
    missingFields: [],
    ignoredSections: [],
    processingStatus: 'review_required',
  },
}

function renderFlow() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={client}>
      <BillImportFlow
        home={home}
        services={[service]}
        onClose={() => undefined}
      />
    </QueryClientProvider>,
  )
}

function parsedBody(options: unknown): Record<string, unknown> {
  const body = (options as RequestInit | undefined)?.body
  if (typeof body !== 'string') throw new Error('Expected JSON body')
  return JSON.parse(body) as Record<string, unknown>
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('bill tariff and monitored-usage boundary', () => {
  it('saves only reviewed rate rules and keeps reported usage as collapsed reference evidence', async () => {
    requestMock.mockImplementation((path: string) => {
      if (path.startsWith('/api/v1/admin/utility-bill-imports?')) return Promise.resolve(bill)
      if (path === '/api/v1/admin/utility-bill-imports/bill-1/review') {
        return Promise.resolve({ ...bill, revision: 2, status: 'ready_to_publish' })
      }
      if (path === '/api/v1/admin/utility-bill-imports/bill-1/validate') {
        return Promise.resolve({ validation: { valid: true }, blocking_warnings: [] })
      }
      if (path === '/api/v1/admin/utility-bill-imports/bill-1/publish-and-assign') {
        return Promise.resolve({ status: 'published' })
      }
      throw new Error(`Unexpected request: ${path}`)
    })

    const user = userEvent.setup()
    renderFlow()

    expect(screen.getByText(/Rates from your bill; usage from your sensors/)).toBeVisible()
    expect(screen.getByText(/reported kWh and total amount are never used/)).toBeVisible()

    const upload = document.querySelector('input[type="file"]') as HTMLInputElement
    expect(upload).not.toBeNull()
    await user.upload(upload, new File(['pdf'], 'bill.pdf', { type: 'application/pdf' }))
    await user.click(screen.getByRole('button', { name: 'Upload and review' }))

    expect(await screen.findByText('Tier 1 Price')).toBeVisible()
    const referenceSummary = screen.getByText('Reference information from uploaded bill')
    expect(referenceSummary).toBeVisible()
    expect(screen.getByText('Bill-Reported Usage')).not.toBeVisible()

    await user.click(referenceSummary)
    expect(screen.getByText('Bill-Reported Usage')).toBeVisible()
    expect(screen.getAllByText(/Not used in calculation/).length).toBeGreaterThan(0)

    await user.click(screen.getByRole('button', { name: 'Review rate rules' }))
    await screen.findByRole('heading', { name: 'Save reviewed rate rules' })

    const reviewCall = requestMock.mock.calls.find(
      (call) => call[0] === '/api/v1/admin/utility-bill-imports/bill-1/review',
    )
    const reviewBody = parsedBody(reviewCall?.[1])
    expect(reviewBody.field_reviews).toEqual([
      { field_id: 'field-rate', action: 'confirm' },
    ])

    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: 'Save rate rules' }))

    expect(await screen.findByRole('heading', { name: 'Rate rules saved' })).toBeVisible()
    expect(screen.getByText(/Bill-reported usage and totals remain reference evidence/))
      .toBeVisible()
    await waitFor(() => {
      expect(requestMock).toHaveBeenCalledWith(
        '/api/v1/admin/utility-bill-imports/bill-1/publish-and-assign',
        expect.objectContaining({ method: 'POST' }),
      )
    })
    expect(requestMock.mock.calls.some(
      (call) => String(call[0]).includes('apply-cycle-dates'),
    )).toBe(false)
  })
})
