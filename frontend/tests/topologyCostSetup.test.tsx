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

import { CostCalculationSetup } from '../src/features/billing/CostCalculationSetup'
import { MeasurementAssignmentDialog } from '../src/features/sensors/MeasurementAssignmentDialog'
import type {
  BillingCycleSummary,
  ElectricService,
  Home,
  SensorSummary,
} from '../src/types/models'

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
    rate: 'ready',
    cost: 'setup_needed',
    topologyComplete: false,
  },
}

const sensor: SensorSummary = {
  id: 'sensor-1',
  name: 'Indoor AC',
  homeId: home.id,
  state: 'waiting',
  deviceStatus: 'online_waiting',
  online: true,
  measurementFreshness: 'waiting',
  heartbeatFreshness: 'online',
  offlineAfterSeconds: 30,
  invalidMetrics: [],
  monitoredCircuit: 'Unassigned',
  includedInDefault: false,
  backlog: 0,
  ctRatingAmps: '100',
  measurementRole: 'branch',
}

const cycle: BillingCycleSummary = {
  available: false,
  id: 'cycle-1',
  startsAt: '2026-07-01T07:00:00Z',
  endsAt: '2026-08-01T07:00:00Z',
  recalculationVersion: 0,
  pricingModel: 'tiered',
  tiers: [],
  warnings: ['Chronological tier allocation has not been calculated.'],
  usageSourceType: 'unavailable',
  billUsageCalculationRole: 'reference_only',
  projectionSourceType: 'unavailable',
  tierProgressSourceType: 'unavailable',
  recalculationRequired: false,
  legacyBillAuthorityReviewRequired: false,
}

function parseBody(options: unknown): Record<string, unknown> {
  const body = (options as RequestInit | undefined)?.body
  if (typeof body !== 'string') throw new Error('Expected a JSON request body')
  return JSON.parse(body) as Record<string, unknown>
}

function renderWithClient(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)
}

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

describe('sensor topology and tier allocation setup', () => {
  it('creates a physical circuit and assigns the existing sensor to its service', async () => {
    const onDone = vi.fn()
    requestMock.mockImplementation((
      path: string,
      options?: unknown,
      adapter?: (value: unknown) => unknown,
    ) => {
      let value: unknown
      if (path.startsWith('/api/v1/circuits?')) value = []
      else if (path === '/api/v1/circuits') {
        value = {
          id: 'circuit-1',
          site_id: home.id,
          parent_id: null,
          name: 'Indoor AC',
          measurement_role: 'branch',
          split_phase_group: null,
        }
      } else if (path === '/api/v1/admin/devices/sensor-1/measurement-assignment') {
        expect(parseBody(options)).toMatchObject({
          circuit_id: 'circuit-1',
          utility_account_id: service.id,
          include_in_default_site_total: false,
        })
        value = { device_id: sensor.id }
      } else throw new Error(`Unexpected request: ${path}`)
      return Promise.resolve(adapter ? adapter(value) : value)
    })

    const user = userEvent.setup()
    renderWithClient(
      <MeasurementAssignmentDialog
        home={home}
        sensor={sensor}
        services={[service]}
        onClose={() => undefined}
        onDone={onDone}
      />,
    )

    expect(await screen.findByRole('heading', { name: 'Manage Indoor AC assignment' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Save assignment' }))

    await waitFor(() => { expect(onDone).toHaveBeenCalledTimes(1) })
    expect(requestMock).toHaveBeenCalledWith(
      '/api/v1/circuits',
      expect.objectContaining({ method: 'POST' }),
      expect.any(Function),
    )
    expect(requestMock).toHaveBeenCalledWith(
      '/api/v1/admin/devices/sensor-1/measurement-assignment',
      expect.objectContaining({ method: 'PUT' }),
    )
  })

  it('unassigns a sensor while preserving its historical readings', async () => {
    const onDone = vi.fn()
    vi.stubGlobal('confirm', vi.fn(() => true))
    requestMock.mockImplementation((path: string, options?: unknown) => {
      if (path.startsWith('/api/v1/circuits?')) {
        return Promise.resolve([{
          id: 'circuit-1',
          site_id: home.id,
          parent_id: null,
          name: 'Indoor AC',
          measurement_role: 'branch',
          split_phase_group: null,
        }])
      }
      if (path === '/api/v1/admin/devices/sensor-1/measurement-assignment') {
        expect(parseBody(options)).toEqual({
          circuit_id: null,
          utility_account_id: null,
          include_in_default_site_total: false,
          reason: 'Owner reviewed the sensor measurement boundary',
        })
        return Promise.resolve({ device_id: sensor.id })
      }
      throw new Error(`Unexpected request: ${path}`)
    })

    const user = userEvent.setup()
    renderWithClient(
      <MeasurementAssignmentDialog
        home={home}
        sensor={{
          ...sensor,
          circuitId: 'circuit-1',
          utilityAccountId: service.id,
          includedInDefault: true,
        }}
        services={[service]}
        onClose={() => undefined}
        onDone={onDone}
      />,
    )

    await user.click(await screen.findByRole('button', { name: 'Unassign sensor' }))

    await waitFor(() => { expect(onDone).toHaveBeenCalledTimes(1) })
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('Historical readings'))
    expect(requestMock).toHaveBeenCalledWith(
      '/api/v1/admin/devices/sensor-1/measurement-assignment',
      expect.objectContaining({ method: 'PUT' }),
    )
  })

  it('saves a sensor measurement authority before recalculating the current cycle', async () => {
    requestMock.mockImplementation((
      path: string,
      options?: unknown,
      adapter?: (value: unknown) => unknown,
    ) => {
      let value: unknown
      if (path === '/api/v1/admin/utility-accounts/service-1/usage-authority') {
        if ((options as RequestInit | undefined)?.method === 'PUT') {
          expect(parseBody(options)).toMatchObject({
            authority_type: 'whole_account_meter',
            device_ids: ['sensor-1'],
            source_reference: null,
            confidence: 'high',
            complete_account: true,
            calculation_role: 'sensor_measurements',
          })
          value = {
            configured: true,
            authority_type: 'whole_account_meter',
            calculation_role: 'sensor_measurements',
            device_ids: ['sensor-1'],
            source_reference: null,
            confidence: 'high',
            complete_account: true,
            revision: 1,
            valid_device_ids: ['sensor-1'],
            invalid_devices: [],
            stored_authority_healthy: true,
            account_assigned_sensors: [{
              id: 'sensor-1',
              name: 'Indoor AC',
              lifecycle: 'active',
              site_id: home.id,
              utility_account_id: service.id,
              measurement_role: 'main',
              circuit_id: 'main-circuit',
              circuit_name: 'Main service',
              circuit_role: 'main',
              whole_account_reason: 'eligible',
              service_leg_reason: 'wrong_measurement_role',
            }],
            eligible_whole_account_sensors: [{
              id: 'sensor-1',
              name: 'Indoor AC',
              lifecycle: 'active',
              site_id: home.id,
              utility_account_id: service.id,
              measurement_role: 'main',
              circuit_id: 'main-circuit',
              circuit_name: 'Main service',
              circuit_role: 'main',
              whole_account_reason: 'eligible',
              service_leg_reason: 'wrong_measurement_role',
            }],
            eligible_service_leg_sensors: [],
            recommended_repair: 'No authority repair is required.',
          }
        } else {
          value = {
            configured: false,
            authority_type: null,
            calculation_role: 'unavailable',
            device_ids: [],
            confidence: 'unknown',
            complete_account: false,
            revision: 0,
            valid_device_ids: [],
            invalid_devices: [],
            stored_authority_healthy: false,
            account_assigned_sensors: [{
              id: 'sensor-1',
              name: 'Indoor AC',
              lifecycle: 'active',
              site_id: home.id,
              utility_account_id: service.id,
              measurement_role: 'main',
              circuit_id: 'main-circuit',
              circuit_name: 'Main service',
              circuit_role: 'main',
              whole_account_reason: 'eligible',
              service_leg_reason: 'wrong_measurement_role',
            }],
            eligible_whole_account_sensors: [{
              id: 'sensor-1',
              name: 'Indoor AC',
              lifecycle: 'active',
              site_id: home.id,
              utility_account_id: service.id,
              measurement_role: 'main',
              circuit_id: 'main-circuit',
              circuit_name: 'Main service',
              circuit_role: 'main',
              whole_account_reason: 'eligible',
              service_leg_reason: 'wrong_measurement_role',
            }],
            eligible_service_leg_sensors: [],
            recommended_repair: 'Select one verified whole-account meter.',
          }
        }
      } else if (
        path === '/api/v1/admin/utility-accounts/service-1/billing-cycles/current/recalculate'
      ) {
        value = {
          available: true,
          cycle: {
            id: cycle.id,
            starts_at: cycle.startsAt,
            ends_at: cycle.endsAt,
            status: 'confirmed',
          },
          recalculation_version: 1,
          pricing_model: 'tiered',
          usage_source_type: 'sensor_measurements',
          projection_source_type: 'sensor_trend',
          tier_progress_source_type: 'sensor_measurements',
          tiers: [],
          warnings: [],
        }
      } else throw new Error(`Unexpected request: ${path}`)
      return Promise.resolve(adapter ? adapter(value) : value)
    })

    const user = userEvent.setup()
    renderWithClient(
      <CostCalculationSetup
        service={service}
        sensors={[{ ...sensor, utilityAccountId: service.id, measurementRole: 'main' }]}
        cycle={cycle}
        onRefresh={() => Promise.resolve()}
      />,
    )

    await screen.findByText('Sensor usage source not configured')
    await user.click(screen.getByRole('button', { name: 'Save and recalculate' }))

    expect(await screen.findByText(/Sensor usage and chronological tier allocation recalculated at version 1/))
      .toBeVisible()
    const putIndex = requestMock.mock.calls.findIndex((call) => (
      call[0] === '/api/v1/admin/utility-accounts/service-1/usage-authority'
      && (call[1] as RequestInit | undefined)?.method === 'PUT'
    ))
    const recalculateIndex = requestMock.mock.calls.findIndex((call) => (
      call[0] === '/api/v1/admin/utility-accounts/service-1/billing-cycles/current/recalculate'
    ))
    expect(putIndex).toBeGreaterThanOrEqual(0)
    expect(recalculateIndex).toBeGreaterThanOrEqual(0)
    expect(putIndex).toBeLessThan(recalculateIndex)
  })

  it('does not count a stale hidden authority ID against the service-leg limit', async () => {
    const authoritySensor = (id: string, name: string) => ({
      id,
      name,
      lifecycle: 'active',
      site_id: home.id,
      utility_account_id: service.id,
      measurement_role: 'service-leg',
      circuit_id: `circuit-${id}`,
      circuit_name: `${name} service leg`,
      circuit_role: 'service-leg',
      split_phase_group: 'service-main',
      whole_account_reason: 'wrong_measurement_role',
      service_leg_reason: 'eligible',
    })
    requestMock.mockImplementation((
      path: string,
      _options?: unknown,
      adapter?: (value: unknown) => unknown,
    ) => {
      if (path !== '/api/v1/admin/utility-accounts/service-1/usage-authority') {
        throw new Error(`Unexpected request: ${path}`)
      }
      const value = {
        configured: true,
        authority_type: 'service_leg_pair',
        calculation_role: 'sensor_measurements',
        device_ids: ['leg-1', 'removed-sensor'],
        valid_device_ids: ['leg-1'],
        invalid_devices: [{
          device_id: 'removed-sensor',
          name: 'Removed leg',
          reason: 'stale_reference',
        }],
        stored_authority_healthy: false,
        account_assigned_sensors: [
          authoritySensor('leg-1', 'Leg one'),
          authoritySensor('leg-2', 'Leg two'),
        ],
        eligible_whole_account_sensors: [],
        eligible_service_leg_sensors: [
          authoritySensor('leg-1', 'Leg one'),
          authoritySensor('leg-2', 'Leg two'),
        ],
        complete_account: true,
        confidence: 'high',
        revision: 4,
        recommended_repair: 'Choose the current service-leg sensors.',
      }
      return Promise.resolve(adapter ? adapter(value) : value)
    })

    const leg = (id: string, name: string): SensorSummary => ({
      ...sensor,
      id,
      name,
      utilityAccountId: service.id,
      measurementRole: 'service-leg',
      monitoredCircuit: `${name} service leg`,
    })
    const user = userEvent.setup()
    renderWithClient(
      <CostCalculationSetup
        service={service}
        sensors={[leg('leg-1', 'Leg one'), leg('leg-2', 'Leg two')]}
        cycle={cycle}
        onRefresh={() => Promise.resolve()}
      />,
    )

    expect(await screen.findByText(/saved usage source references a sensor/i)).toBeVisible()
    const first = screen.getByRole('checkbox', { name: /Leg one/i })
    const second = screen.getByRole('checkbox', { name: /Leg two/i })
    expect(first).toBeChecked()
    expect(second).toBeEnabled()
    await user.click(second)
    expect(screen.getByRole('button', { name: 'Save and recalculate' })).toBeEnabled()
  })
})
