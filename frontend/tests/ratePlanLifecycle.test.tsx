import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { findDuplicateActions } from '../src/actions'
import { RatesPage } from '../src/pages/RatesPage'
import type { ManagedRatePlan } from '../src/rates'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function response(value: unknown, status = 200) {
  return Promise.resolve(
    new Response(status === 204 ? null : JSON.stringify(value), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <RatesPage canManage={false} canRemove canRestore />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

const activePlan: ManagedRatePlan = {
  id: 'plan-1',
  code: 'CUSTOM-LIFECYCLE',
  name: 'Custom lifecycle plan',
  description: 'Dependency-aware lifecycle fixture',
  plan_kind: 'custom',
  ownership_scope: 'global',
  currency: 'USD',
  timezone: 'America/Los_Angeles',
  status: 'active',
  lifecycle_revision: 2,
  versions: [{
    id: 'version-1',
    version: 1,
    pricing_model: 'tiered',
    tier_count: 2,
    threshold_basis: 'fixed_cycle_kwh',
    effective_from: '2026-07-01',
    status: 'active',
    source_kind: 'custom',
    source_label: 'Administrator-defined plan',
    integrity_sha256: 'a'.repeat(64),
    is_active: true,
    immutable: true,
    created_at: '2026-07-01T00:00:00Z',
  }],
}

const removedPlan: ManagedRatePlan = {
  ...activePlan,
  status: 'removed',
  lifecycle_revision: 3,
  removed_at: '2026-07-24T10:00:00Z',
  removed_by: 'admin-1',
  removal_reason: 'No longer offered',
}

function dependencies(blocked = false) {
  return {
    plan_id: activePlan.id,
    plan_code: activePlan.code,
    plan_name: activePlan.name,
    plan_kind: 'custom',
    origin: 'custom',
    status: activePlan.status,
    lifecycle_revision: activePlan.lifecycle_revision,
    version_count: 1,
    active_assignments: blocked
      ? [{ id: 'assignment-1', utility_account_id: 'account-1' }]
      : [],
    future_assignments: [],
    active_account_pointers: [],
    historical_assignment_count: 4,
    historical_calculation_count: 12,
    report_count: 2,
    source_evidence_count: 3,
    bill_import_count: 1,
    managed_candidate_count: 0,
    cloned_plan_count: 0,
    candidate_version_reference_count: 0,
    permanent_draft_deletion_eligible: false,
    removal_blocked: blocked,
    dependency_actions: blocked
      ? [
          'replace_assignment',
          'schedule_replacement',
          'end_future_assignment',
          'cancel_removal',
        ]
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

function mockApi(options: { blocked?: boolean } = {}) {
  const requests: Array<{ url: string; method: string; body?: unknown }> = []
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url =
        typeof input === 'string'
          ? input
          : input instanceof URL
            ? input.href
            : input.url
      const method = init?.method ?? 'GET'
      requests.push({
        url,
        method,
        body:
          typeof init?.body === 'string'
            ? (JSON.parse(init.body) as unknown)
            : undefined,
      })
      if (url.includes('/api/v1/rates/plans?status=removed_or_retired')) {
        return response([removedPlan])
      }
      if (url.includes('/api/v1/rates/plans?status=active')) {
        return response([activePlan])
      }
      if (url.endsWith('/api/v1/rates/assignments')) return response([])
      if (url.endsWith('/api/v1/utility-accounts')) return response([])
      if (url.endsWith('/api/v1/sites')) return response([])
      if (url.endsWith('/dependencies')) {
        return response(dependencies(options.blocked))
      }
      if (url.endsWith('/remove')) {
        return response({
          idempotent: false,
          plan: removedPlan,
          dependencies: dependencies(false),
        })
      }
      if (url.endsWith('/restore')) {
        return response({
          idempotent: false,
          plan: activePlan,
          assignments_restored: false,
        })
      }
      return response({})
    }),
  )
  return requests
}

describe('rate-plan lifecycle interface', () => {
  it('shows one canonical Remove action with dependency and preservation review', async () => {
    const requests = mockApi()
    renderPage()
    fireEvent.click(
      await screen.findByRole('button', { name: 'Remove rate plan' }),
    )

    expect(await screen.findByText('Historical calculations')).toBeVisible()
    expect(screen.getByText('12')).toBeVisible()
    expect(screen.getByText('Historical records are retained')).toBeVisible()
    expect(
      screen.getAllByRole('button', { name: 'Remove rate plan' }),
    ).toHaveLength(1)
    expect(findDuplicateActions()).toEqual([])

    fireEvent.change(screen.getByLabelText('Removal reason'), {
      target: { value: 'Replace an obsolete custom plan' },
    })
    fireEvent.change(screen.getByLabelText(/Type exact plan name or code/), {
      target: { value: activePlan.code },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Remove rate plan' }))
    await waitFor(() => {
      expect(
        requests.some(
          (item) => item.url.endsWith('/remove') && item.method === 'POST',
        ),
      ).toBe(true)
    })
  })

  it('replaces the destructive action with dependency resolution when assigned', async () => {
    mockApi({ blocked: true })
    renderPage()
    fireEvent.click(
      await screen.findByRole('button', { name: 'Remove rate plan' }),
    )
    expect(await screen.findByText('Removal is blocked')).toBeVisible()
    expect(
      screen.getByRole('button', { name: 'Resolve assignments' }),
    ).toBeVisible()
    expect(
      screen.queryByRole('button', { name: 'Remove rate plan' }),
    ).not.toBeInTheDocument()
    expect(findDuplicateActions()).toEqual([])
  })

  it('lists removed plans and restores without reassigning accounts', async () => {
    const requests = mockApi()
    renderPage()
    fireEvent.click(
      await screen.findByRole('button', { name: 'Removed / Retired' }),
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Restore' }))
    expect(await screen.findByText('Restore CUSTOM-LIFECYCLE?')).toBeVisible()
    expect(
      screen.getByText('Future assignments disabled'),
    ).toBeInTheDocument()
    fireEvent.change(await screen.findByLabelText('Restore reason'), {
      target: { value: 'Return plan to future selection' },
    })
    expect(
      screen.getAllByRole('button', { name: 'Restore rate plan' }),
    ).toHaveLength(1)
    expect(findDuplicateActions()).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: 'Restore rate plan' }))
    await waitFor(() => {
      expect(
        requests.some(
          (item) => item.url.endsWith('/restore') && item.method === 'POST',
        ),
      ).toBe(true)
    })
  })
})
