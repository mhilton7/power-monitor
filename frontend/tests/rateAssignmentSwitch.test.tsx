import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RatesPage } from '../src/pages/RatesPage'
import type { ManagedRatePlan } from '../src/rates'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function response(value: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(value), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

function plan(id: string, versionId: string, code: string, name: string): ManagedRatePlan {
  return {
    id,
    code,
    name,
    description: `${name} fixture`,
    plan_kind: 'official_sce',
    ownership_scope: 'global',
    currency: 'USD',
    timezone: 'America/Los_Angeles',
    status: 'active',
    lifecycle_revision: 1,
    versions: [{
      id: versionId,
      version: 1,
      pricing_model: 'time_of_use',
      tier_count: 0,
      effective_from: '2026-07-01',
      status: 'active',
      source_kind: 'official_sce',
      source_label: 'SCE evidence',
      integrity_sha256: 'a'.repeat(64),
      is_active: true,
      immutable: true,
      created_at: '2026-07-01T00:00:00Z',
    }],
  }
}

describe('active rate-plan switching', () => {
  it('replaces the selected account assignment and reports the new active plan', async () => {
    const first = plan('plan-1', 'version-1', 'TOU-FIRST', 'First plan')
    const second = plan('plan-2', 'version-2', 'TOU-SECOND', 'Second plan')
    let assignments: Array<{
      id: string
      utility_account_id: string
      rate_version_id: string
      effective_from: string
      effective_to?: string
    }> = [{
      id: 'assignment-1',
      utility_account_id: 'account-1',
      rate_version_id: 'version-1',
      effective_from: '2026-07-01T00:00:00Z',
      effective_to: undefined,
    }]
    const requests: Array<{ url: string; method: string; body?: Record<string, unknown> }> = []
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
        const body =
          typeof init?.body === 'string'
            ? JSON.parse(init.body) as Record<string, unknown>
            : undefined
        requests.push({ url, method, body })
        if (url.includes('/api/v1/rates/plans?status=active')) {
          return response([first, second])
        }
        if (url.endsWith('/api/v1/rates/assignments')) {
          return response(assignments)
        }
        if (url.endsWith('/api/v1/utility-accounts')) {
          return response([{
            id: 'account-1',
            name: 'Home',
            site_id: 'site-1',
            status: 'active',
            active_rate_version_id: assignments.at(-1)?.rate_version_id,
          }])
        }
        if (url.endsWith('/api/v1/sites')) {
          return response([{ id: 'site-1', name: 'Home', timezone: 'America/Los_Angeles' }])
        }
        if (url.endsWith('/api/v1/admin/rate-candidates')) return response([])
        if (
          url.endsWith('/api/v1/admin/utility-accounts/account-1/rate-assignments')
          && method === 'POST'
        ) {
          const effectiveFrom = String(body?.effective_from)
          const previous = assignments[0]
          if (!previous) return response({ detail: 'Missing current assignment' }, 500)
          assignments = [
            { ...previous, effective_to: effectiveFrom },
            {
              id: 'assignment-2',
              utility_account_id: 'account-1',
              rate_version_id: 'version-2',
              effective_from: effectiveFrom,
              effective_to: undefined,
            },
          ]
          return response({
            id: 'assignment-2',
            effective_from: effectiveFrom,
            effective_to: null,
            effective_now: true,
            replaced_assignment_ids: ['assignment-1'],
          }, 201)
        }
        return response({})
      }),
    )
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <RatesPage canManage />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    const secondCard = (await screen.findByText('Second plan')).closest('.rate-card')
    expect(secondCard).not.toBeNull()
    fireEvent.click(within(secondCard as HTMLElement).getByRole('button', { name: 'Use this plan' }))

    const dialog = await screen.findByRole('dialog', { name: 'Use Second plan' })
    expect(within(dialog).getByText('First plan · v1')).toBeVisible()
    expect(within(dialog).getByLabelText('Utility account')).toHaveValue('account-1')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Switch active plan' }))

    expect(
      await screen.findByText(
        'Second plan is now active for Home. The previous assignment remains in history.',
      ),
    ).toBeVisible()
    await waitFor(() => {
      const request = requests.find(
        (item) =>
          item.url.endsWith('/admin/utility-accounts/account-1/rate-assignments')
          && item.method === 'POST',
      )
      expect(request?.body).toMatchObject({
        rate_version_id: 'version-2',
        replace_current: true,
        assignment_reason: 'Administrator selected a new active rate plan',
      })
    })
  })
})
