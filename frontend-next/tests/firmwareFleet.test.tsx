import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const runtime = vi.hoisted(() => ({
  permissions: ['firmware.view', 'firmware.manage', 'firmware.deploy'] as string[],
  sensors: [] as Array<Record<string, unknown>>,
}))

vi.mock('../src/state/AuthContext', () => ({
  useAuth: () => ({
    session: {
      authenticated: true,
      bootstrapRequired: false,
      user: {
        id: 'owner-1', email: 'owner@example.test', name: 'Owner', roles: ['admin'],
        permissions: runtime.permissions, allHomes: true, homeIds: [], accessRevision: 1,
      },
    },
  }),
}))

vi.mock('../src/state/LiveHomeContext', () => ({
  useLiveHome: () => ({ sensors: runtime.sensors }),
}))

import { FirmwareFleetWorkflow } from '../src/features/firmware/FirmwareFleetWorkflow'

const jsonHeaders = { 'Content-Type': 'application/json' }

function sensor(id: string, name: string, firmware = '1.0.10', state = 'ready') {
  return {
    id,
    name,
    firmware,
    firmwareOta: {
      state,
      supported: state === 'ready',
      protocolVersion: state === 'ready' ? 2 : undefined,
      authenticationMode: state === 'ready' ? 'existing_device_hmac' : undefined,
      rollbackSupported: state === 'ready',
      partitionSizeBytes: state === 'ready' ? 6_291_456 : undefined,
    },
  }
}

function release() {
  return {
    id: 'release-11', version: '1.0.11', project_name: 'power-monitor-sensor',
    hardware_target: 'esp32-s3', protocol_min: 'pm-protocol/1.0.0', protocol_max: 'pm-protocol/1.0.0',
    size_bytes: 1_650_000, sha256: 'b'.repeat(64), build_hash: 'build-11',
    trust_mode: 'existing_device_hmac', verification_status: 'verified', active: true,
  }
}

function deployment(overrides: Record<string, unknown> = {}) {
  return {
    id: 'deployment-1', firmware_release_id: 'release-11', device_id: 'sensor-outdoor',
    state: 'scheduled', revision: 1, attempt: 1, progress: 10, bytes_received: 0,
    target_version: '1.0.11', verification_heartbeats: 0,
    ...overrides,
  }
}

function view() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><FirmwareFleetWorkflow /></QueryClientProvider>)
}

function chooseFirmware() {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]')
  expect(input).not.toBeNull()
  fireEvent.change(input as HTMLInputElement, {
    target: { files: [new File(['firmware'], 'firmware.bin', { type: 'application/octet-stream' })] },
  })
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.toString()
  return input.url
}

beforeEach(() => {
  runtime.permissions = ['firmware.view', 'firmware.manage', 'firmware.deploy']
  runtime.sensors = [
    sensor('sensor-indoor', 'Indoor-AC'),
    sensor('sensor-outdoor', 'Outdoor-AC'),
    sensor('sensor-current', 'Garage', '1.0.11'),
    sensor('sensor-legacy', 'Legacy meter', '1.0.9', 'bootstrap_required'),
  ]
})

afterEach(() => { vi.unstubAllGlobals() })

describe('advanced existing-trust firmware rollout', () => {
  it('requires explicit targets and sends a canary-first, maximum-concurrency-one rollout', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      calls.push({ url, init })
      if (url === '/api/v1/firmware-releases' && init?.method === 'POST') {
        return Promise.resolve(new Response(JSON.stringify(release()), { status: 201, headers: jsonHeaders }))
      }
      if (url === '/api/v1/firmware-deployments' && init?.method === 'POST') {
        return Promise.resolve(new Response(JSON.stringify({ deployments: [deployment()] }), { status: 201, headers: jsonHeaders }))
      }
      if (url === '/api/v1/firmware-releases') return Promise.resolve(new Response('[]', { status: 200, headers: jsonHeaders }))
      if (url === '/api/v1/firmware-deployments') return Promise.resolve(new Response('[]', { status: 200, headers: jsonHeaders }))
      throw new Error(`Unexpected request ${url}`)
    }))
    const user = userEvent.setup()
    view()

    chooseFirmware()
    await user.click(screen.getByRole('button', { name: 'Verify firmware' }))
    expect(await screen.findByText(/1.0.11 verified for esp32-s3/)).toBeVisible()

    const indoor = screen.getByRole('checkbox', { name: /Indoor-AC/ })
    const outdoor = screen.getByRole('checkbox', { name: /Outdoor-AC/ })
    expect(indoor).not.toBeChecked()
    expect(outdoor).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: /Garage/ })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: /Legacy meter/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Install on 0 sensors' })).toBeDisabled()

    await user.click(outdoor)
    await user.click(indoor)
    expect(within(outdoor.closest('label') as HTMLElement).getByText('Canary')).toBeVisible()
    expect(within(indoor.closest('label') as HTMLElement).queryByText('Canary')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Install on 2 sensors' }))

    await waitFor(() => {
      const call = calls.find((item) => item.url === '/api/v1/firmware-deployments' && item.init?.method === 'POST')
      expect(typeof call?.init?.body).toBe('string')
      expect(JSON.parse(call?.init?.body as string)).toMatchObject({
        firmware_release_id: 'release-11',
        device_ids: ['sensor-outdoor', 'sensor-indoor'],
        canary_first: true,
        maximum_concurrency: 1,
      })
    })
  })

  it('keeps release management and deployment controls separated by effective permissions', async () => {
    runtime.permissions = ['firmware.view']
    vi.stubGlobal('fetch', vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = requestUrl(input)
      if (url === '/api/v1/firmware-releases') return Promise.resolve(new Response(JSON.stringify([release()]), { status: 200, headers: jsonHeaders }))
      if (url === '/api/v1/firmware-deployments') return Promise.resolve(new Response(JSON.stringify([
        deployment(),
        deployment({ id: 'deployment-failed', state: 'failed', failure_code: 'download_timeout' }),
      ]), { status: 200, headers: jsonHeaders }))
      throw new Error(`Unexpected request ${url}`)
    }))
    view()

    expect(await screen.findByRole('heading', { name: 'Verified releases' })).toBeVisible()
    expect((await screen.findAllByText('1.0.11')).length).toBeGreaterThan(0)
    expect(screen.queryByText('Prepare a multi-sensor release')).not.toBeInTheDocument()
    expect(screen.queryByText('Choose firmware.bin')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Install on/ })).not.toBeInTheDocument()
  })

  it('keeps canary promotion disabled until heartbeat and reading gates pass, then promotes next', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    let gateOpen = false
    vi.stubGlobal('fetch', vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      calls.push({ url, init })
      if (url.endsWith('/promote') && init?.method === 'POST') {
        return Promise.resolve(new Response(JSON.stringify({ promoted: true, rollout_complete: false }), { status: 200, headers: jsonHeaders }))
      }
      if (url === '/api/v1/firmware-releases') return Promise.resolve(new Response(JSON.stringify([release()]), { status: 200, headers: jsonHeaders }))
      if (url === '/api/v1/firmware-deployments') return Promise.resolve(new Response(JSON.stringify([
        deployment({
          id: 'deployment-canary', state: 'completed', rollout_group_id: 'rollout-1', rollout_order: 0,
          verification_heartbeats: gateOpen ? 10 : 9,
          reading_confirmed_at: gateOpen ? '2026-08-02T10:00:00Z' : null,
          progress: 100,
        }),
      ]), { status: 200, headers: jsonHeaders }))
      throw new Error(`Unexpected request ${url}`)
    }))
    const user = userEvent.setup()
    const first = view()

    expect(await screen.findByRole('button', { name: 'Promote next' })).toBeDisabled()
    first.unmount()
    gateOpen = true
    view()

    const promote = await screen.findByRole('button', { name: 'Promote next' })
    expect(promote).toBeEnabled()
    await user.click(promote)
    await waitFor(() => {
      expect(calls.some((item) => item.url === '/api/v1/firmware-deployments/deployment-canary/promote'
        && item.init?.method === 'POST')).toBe(true)
    })
  })

  it('exposes cancel and retry only to firmware deployers and refreshes persisted records', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    let records = [deployment(), deployment({ id: 'deployment-failed', state: 'failed' })]
    vi.stubGlobal('fetch', vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      calls.push({ url, init })
      if (url.endsWith('/cancel') && init?.method === 'POST') {
        records = records.map((item) => item.id === 'deployment-1' ? { ...item, state: 'cancelled' } : item)
        return Promise.resolve(new Response(JSON.stringify(records[0]), { status: 200, headers: jsonHeaders }))
      }
      if (url.endsWith('/retry') && init?.method === 'POST') {
        records = records.map((item) => item.id === 'deployment-failed' ? { ...item, state: 'scheduled', attempt: 2 } : item)
        return Promise.resolve(new Response(JSON.stringify(records[1]), { status: 200, headers: jsonHeaders }))
      }
      if (url === '/api/v1/firmware-releases') return Promise.resolve(new Response(JSON.stringify([release()]), { status: 200, headers: jsonHeaders }))
      if (url === '/api/v1/firmware-deployments') return Promise.resolve(new Response(JSON.stringify(records), { status: 200, headers: jsonHeaders }))
      throw new Error(`Unexpected request ${url}`)
    }))
    const user = userEvent.setup()
    view()

    await user.click(await screen.findByRole('button', { name: 'Cancel' }))
    await waitFor(() => { expect(calls.some((item) => item.url.endsWith('/cancel') && item.init?.method === 'POST')).toBe(true) })
    const failedRow = (await screen.findByText('Failed')).closest('.firmware-deployment-row')
    expect(failedRow).not.toBeNull()
    await user.click(within(failedRow as HTMLElement).getByRole('button', { name: 'Retry' }))
    await waitFor(() => { expect(calls.some((item) => item.url.endsWith('/retry') && item.init?.method === 'POST')).toBe(true) })
  })
})
