import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdvancedRateSettings } from '../src/features/rates/AdvancedRateSettings'
import type { ElectricService, Home } from '../src/types/models'

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
  homeId: 'home-1',
  name: 'Home-Energy',
  provider: 'Southern California Edison',
  currency: 'USD',
  timezone: 'America/Los_Angeles',
  billingDay: 1,
  status: 'active',
  costScope: 'energy_only',
  revision: 1,
  currentPlan: 'Current plan',
  planCode: 'CURRENT',
  rateVersionId: 'version-current',
  currentVersion: 1,
  readiness: {
    rate: 'ready',
    cost: 'waiting_for_readings',
    topologyComplete: false,
  },
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function requestPath(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.toString()
  return input.url
}

function renderRates() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={client}>
      <AdvancedRateSettings home={home} services={[service]} />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('rate assignment, version, and source workflows', () => {
  it('renders Published and Current independently and offers same-plan adjustment', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      await Promise.resolve()
      const path = requestPath(input)
      if (path === '/api/v1/rates/plans') {
        return jsonResponse([
          {
            id: 'plan-current',
            name: 'Current plan',
            code: 'CURRENT',
            status: 'active',
            lifecycle_revision: 1,
            versions: [{
              id: 'version-current',
              version: 1,
              publication_status: 'published',
              assignment_status: 'current',
              display_status: 'current',
              pricing_model: 'time_of_use',
              lifecycle_revision: 1,
              assignments: [{
                id: 'assignment-current',
                utility_account_id: 'service-1',
                rate_version_id: 'version-current',
                effective_from: '2026-07-01T00:00:00Z',
                state: 'current',
                revision: 1,
              }],
            }],
          },
          {
            id: 'plan-available',
            name: 'Available plan',
            code: 'AVAILABLE',
            status: 'active',
            lifecycle_revision: 1,
            versions: [{
              id: 'version-available',
              version: 2,
              publication_status: 'published',
              assignment_status: 'unassigned',
              display_status: 'published',
              pricing_model: 'flat',
              lifecycle_revision: 1,
              assignments: [],
            }],
          },
        ])
      }
      if (path === '/api/v1/rates/assignments/conflicts') {
        return jsonResponse({ conflicts: [] })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderRates()

    expect(await screen.findByText(/Published means available/)).toBeVisible()
    expect(screen.getByText(/CURRENT.*Published v1.*Current v1/)).toBeVisible()
    expect(screen.getByText(/AVAILABLE.*Published v2.*Not current/)).toBeVisible()
    expect(screen.getAllByRole('button', { name: /Adjust rates/i })).toHaveLength(2)
    expect(screen.queryByRole('button', { name: /Clone/i })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Replace current' }))
    expect(screen.getByRole('region', { name: 'Replace current plan' })).toBeVisible()
    expect(screen.getByText(/remains in assignment and cost history/i)).toBeVisible()
    expect(screen.getByText('Historical costs')).toBeVisible()
    expect(screen.getByText('Preserved')).toBeVisible()
  })

  it('executes and renders a completed observable source-check job', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      await Promise.resolve()
      const path = requestPath(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      if (path === '/api/v1/rates/plans') return jsonResponse([])
      if (path === '/api/v1/rates/assignments/conflicts') {
        return jsonResponse({ conflicts: [] })
      }
      if (path === '/api/v1/admin/rate-sources') {
        return jsonResponse({
          sources: [{
            id: 'source-1',
            name: 'SCE residential source',
            url: 'https://www.sce.com/rates',
            parser_id: 'sce_public_tou_html_v1',
            enabled: true,
            candidate_count: 0,
          }],
        })
      }
      if (
        path === '/api/v1/admin/rate-sources/check-runs'
        && method === 'GET'
      ) {
        return jsonResponse([])
      }
      if (
        path === '/api/v1/admin/rate-sources/check-now'
        && method === 'POST'
      ) {
        return jsonResponse({
          job_id: 'job-1',
          status: 'queued',
          deduplicated: false,
          progress: { completed: 0, total: 1 },
        }, 202)
      }
      if (path === '/api/v1/admin/rate-sources/check-runs/job-1') {
        return jsonResponse({
          id: 'job-1',
          status: 'succeeded',
          trigger_type: 'manual',
          requested_at: '2026-07-25T12:00:00Z',
          started_at: '2026-07-25T12:00:01Z',
          completed_at: '2026-07-25T12:00:02Z',
          progress: { completed: 1, total: 1, current_source_id: null },
          sources_attempted: 1,
          successes: 1,
          failures: 0,
          candidates: 1,
          archived_evidence: 1,
          items: [{
            id: 'check-1',
            source_id: 'source-1',
            source_name: 'SCE residential source',
            outcome: 'succeeded',
            checked_at: '2026-07-25T12:00:01Z',
            finished_at: '2026-07-25T12:00:02Z',
            candidate_count: 1,
            artifact_count: 1,
          }],
        })
      }
      throw new Error(`Unexpected request: ${method} ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderRates()

    await user.click(await screen.findByRole('tab', { name: 'Sources' }))
    expect(await screen.findByText('SCE residential source')).toBeVisible()
    await user.click(
      screen.getByRole('button', { name: 'Check rate sources now' }),
    )
    expect(await screen.findByText('Source check completed')).toBeVisible()
    expect(screen.getByText(/1 of 1 sources.*1 candidates.*1 archived/)).toBeVisible()
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/admin/rate-sources/check-now',
        expect.objectContaining({ method: 'POST' }),
      )
    })
  })
})
