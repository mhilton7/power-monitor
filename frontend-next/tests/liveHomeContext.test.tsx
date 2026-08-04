import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const requestMock = vi.hoisted(() => vi.fn())

vi.mock('../src/api/client', () => ({
  request: requestMock,
}))

vi.mock('../src/state/SingleHomeContext', () => ({
  useSingleHome: () => ({
    resolution: {
      state: 'ready',
      home: {
        id: 'home-1',
        name: 'Upland Home',
        timezone: 'America/Los_Angeles',
        currency: 'USD',
        lifecycle: 'active',
        isDefault: true,
        revision: 1,
      },
    },
  }),
}))

vi.mock('../src/state/AuthContext', () => ({
  useAuth: () => ({
    session: {
      authenticated: true,
      user: { id: 'viewer-1', email: 'viewer@example.test', displayName: 'Viewer', roles: ['viewer'], permissions: ['overview.view', 'devices.view', 'utility_accounts.view', 'rates.view', 'usage.view', 'costs.view', 'alerts.view'], accessRevision: 1 },
    },
    loading: false,
    error: undefined,
    refresh: () => Promise.resolve(),
  }),
}))

import { LiveHomeProvider, useLiveHome } from '../src/state/LiveHomeContext'
import { historyReadingEventTouchesQuery } from '../src/state/LiveHomeContext'
import { BrowserRouter } from '../src/app/router'

class FakeEventSource {
  static instances: FakeEventSource[] = []
  readonly url: string
  readonly listeners = new Map<string, Set<(event: Event) => void>>()
  onerror: (() => void) | null = null
  closed = false

  constructor(url: string | URL) {
    this.url = String(url)
    FakeEventSource.instances.push(this)
  }

  addEventListener(name: string, listener: (event: Event) => void) {
    const listeners = this.listeners.get(name) ?? new Set()
    listeners.add(listener)
    this.listeners.set(name, listeners)
  }

  emit(name: string, siteId = 'home-1', extra: Record<string, unknown> = {}) {
    const event = new MessageEvent(name, { data: JSON.stringify({ site_id: siteId, ...extra }) })
    for (const listener of this.listeners.get(name) ?? []) listener(event)
  }

  close() {
    this.closed = true
  }
}

function payload(path: string): unknown {
  if (path.startsWith('/api/v1/devices?')) {
    return [{
      id: 'sensor-1',
      name: 'Indoor-AC',
      status: 'online_synchronized',
      current_watts: '1.0',
      voltage_volts: '120.4',
      current_amps: '0.01',
      frequency_hz: '60.0',
      power_factor: '0.83',
      latest_measurement_at: '2026-07-29T21:55:00Z',
      measurement_freshness: 'live',
      heartbeat_freshness: 'online',
      offline_after_seconds: 30,
      measurement_source: 'heartbeat_live',
      measurement_invalid_metrics: [],
    }]
  }
  if (path.startsWith('/api/v1/fleet/summary?')) {
    return {
      current_load_w: '1.0',
      reporting_devices: 1,
      has_live_data: true,
      latest_data_at: '2026-07-29T21:55:00Z',
      active_alerts: 2,
    }
  }
  if (path === '/api/v1/utility-accounts') return []
  if (path.startsWith('/api/v1/configuration-status?')) {
    return {
      schema_version: 'configuration-status/1.0',
      home_id: 'home-1',
      electric_service_id: null,
      state: 'waiting_for_data',
      label: 'Waiting for data',
      summary: 'The sensor is connected.',
      generated_at: '2026-07-29T21:55:00Z',
      issues: [],
    }
  }
  if (path.startsWith('/api/v1/electric-services/default/current-rate-assignment?')) {
    return {
      schema_version: 'current-rate-assignment/1.0',
      home_id: 'home-1',
      electric_service_id: null,
      assignment: null,
    }
  }
  if (path.startsWith('/api/v1/notifications?')) return { items: [], page: 1, page_size: 200, total: 0 }
  throw new Error(`Unexpected request: ${path}`)
}

function Result() {
  const live = useLiveHome()
  return (
    <output>
      {live.summary?.hasLiveData ? 'Connected' : 'Waiting'}
      {' · '}
      {live.summary?.currentPowerW ?? 'missing'}
      {' · '}
      {live.sensors[0]?.voltageVolts ?? 'missing'}
      {' · '}
      {live.summary?.activeAlerts ?? 'missing'}
    </output>
  )
}

function createTestClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
}

function Providers({ children, client }: { children: ReactNode; client: QueryClient }) {
  return (
    <QueryClientProvider client={client}>
      <BrowserRouter><LiveHomeProvider>{children}</LiveHomeProvider></BrowserRouter>
    </QueryClientProvider>
  )
}

describe('Live Home SSE refresh', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
    requestMock.mockImplementation((
      path: string,
      _options?: unknown,
      adapter?: (value: unknown) => unknown,
    ) => {
      const value = payload(path)
      return Promise.resolve(adapter ? adapter(value) : value)
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('uses one site-scoped stream and refreshes both live queries on reading events', async () => {
    const testClient = createTestClient()
    const view = render(<Providers client={testClient}><Result /></Providers>)
    await screen.findByText('Connected · 1.0 · 120.4 · 0')
    expect(FakeEventSource.instances).toHaveLength(1)
    const source = FakeEventSource.instances[0] as FakeEventSource
    expect(source.url).toBe('/api/v1/events/stream?site_id=home-1')

    const initialDeviceRequests = requestMock.mock.calls.filter(
      ([path]) => String(path).startsWith('/api/v1/devices?'),
    ).length
    const initialFleetRequests = requestMock.mock.calls.filter(
      ([path]) => String(path).startsWith('/api/v1/fleet/summary?'),
    ).length
    const invalidationSpy = vi.spyOn(testClient, 'invalidateQueries')
    const refetchSpy = vi.spyOn(testClient, 'refetchQueries')
    act(() => {
      source.emit('heartbeat')
    })
    await waitFor(() => {
      expect(requestMock.mock.calls.filter(
        ([path]) => String(path).startsWith('/api/v1/devices?'),
      ).length).toBeGreaterThan(initialDeviceRequests)
      expect(requestMock.mock.calls.filter(
        ([path]) => String(path).startsWith('/api/v1/fleet/summary?'),
      ).length).toBeGreaterThan(initialFleetRequests)
    })
    expect(invalidationSpy).not.toHaveBeenCalledWith({ queryKey: ['history'] })
    expect(invalidationSpy).toHaveBeenCalledWith({
      queryKey: ['home-summary', 'viewer-1:1', 'home-1'],
    })
    expect(invalidationSpy).toHaveBeenCalledWith({
      queryKey: ['sensors', 'viewer-1:1', 'home-1'],
    })

    const deviceRequestsAfterHeartbeat = requestMock.mock.calls.filter(
      ([path]) => String(path).startsWith('/api/v1/devices?'),
    ).length
    const fleetRequestsAfterHeartbeat = requestMock.mock.calls.filter(
      ([path]) => String(path).startsWith('/api/v1/fleet/summary?'),
    ).length
    act(() => {
      source.emit('reading')
      source.emit('reading')
      source.emit('reading')
    })
    await waitFor(() => {
      expect(requestMock.mock.calls.filter(
        ([path]) => String(path).startsWith('/api/v1/devices?'),
      ).length).toBeGreaterThan(deviceRequestsAfterHeartbeat)
      expect(requestMock.mock.calls.filter(
        ([path]) => String(path).startsWith('/api/v1/fleet/summary?'),
      ).length).toBeGreaterThan(fleetRequestsAfterHeartbeat)
    })
    await waitFor(() => {
      expect(refetchSpy.mock.calls.filter(
        ([filters]) => filters?.type === 'active' && typeof filters.predicate === 'function',
      )).toHaveLength(1)
    }, { timeout: 1_500 })
    const historyRefetches = refetchSpy.mock.calls.filter(
      ([filters]) => filters?.type === 'active' && typeof filters.predicate === 'function',
    )
    expect(historyRefetches[0]?.[0]).toMatchObject({ type: 'active' })
    expect(historyRefetches[0]?.[1]).toEqual({ cancelRefetch: false })
    const predicate = historyRefetches[0]?.[0]?.predicate
    expect(predicate?.({
      getObserversCount: () => 1,
      queryKey: ['history', 'page', 'home-1'],
    } as never)).toBe(true)
    expect(predicate?.({
      getObserversCount: () => 1,
      queryKey: ['history', 'home-daily', 'home-1'],
    } as never)).toBe(true)
    expect(predicate?.({
      getObserversCount: () => 1,
      queryKey: ['history', 'page', 'another-home'],
    } as never)).toBe(false)
    expect(invalidationSpy).not.toHaveBeenCalledWith({ queryKey: ['history'] })

    act(() => {
      source.emit('reading', 'another-home')
    })
    await new Promise((resolve) => window.setTimeout(resolve, 800))
    expect(refetchSpy.mock.calls.filter(
      ([filters]) => filters?.type === 'active' && typeof filters.predicate === 'function',
    )).toHaveLength(1)

    const devicesAfterReading = requestMock.mock.calls.filter(
      ([path]) => String(path).startsWith('/api/v1/devices?'),
    ).length
    act(() => {
      source.emit('device_status')
    })
    await waitFor(() => {
      expect(requestMock.mock.calls.filter(
        ([path]) => String(path).startsWith('/api/v1/devices?'),
      ).length).toBeGreaterThan(devicesAfterReading)
    })

    act(() => {
      source.onerror?.()
    })
    expect(source.closed).toBe(false)
    view.unmount()
    expect(source.closed).toBe(true)
  })

  it('refreshes only history ranges touched by a reading event', () => {
    const payload = {
      interval_start: '2026-07-29T21:55:00Z',
      interval_end: '2026-07-29T21:56:00Z',
    }
    const customKey = [
      'history', 'page', 'home-1', 'custom', 'energy', 'home', null, null, null,
      '2026-07-29T20:00:00Z', '2026-07-29T22:00:00Z', '1h',
    ]
    expect(historyReadingEventTouchesQuery(customKey, payload)).toBe(true)
    expect(historyReadingEventTouchesQuery([
      ...customKey.slice(0, 9),
      '2026-07-28T20:00:00Z', '2026-07-28T22:00:00Z', '1h',
    ], payload)).toBe(false)
    expect(historyReadingEventTouchesQuery([
      'history', 'home-daily', 'home-1',
      '2026-07-29T00:00:00Z', '2026-07-29T21:54:00Z',
    ], payload, Date.parse('2026-07-29T21:56:00Z'))).toBe(true)
  })
})
