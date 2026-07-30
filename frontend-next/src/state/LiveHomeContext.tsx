import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, useEffect, useMemo, type ReactNode } from 'react'
import {
  adaptAlerts,
  adaptBillingCycle,
  adaptConfigurationStatus,
  adaptCurrentRateAssignment,
  adaptElectricServices,
  adaptHomeSummary,
  adaptSensors,
} from '../api/adapters'
import { request } from '../api/client'
import type {
  AlertSummary,
  BillingCycleSummary,
  ElectricService,
  ConfigurationStatus,
  CurrentRateAssignment,
  HomeSummary,
  SensorSummary,
} from '../types/models'
import { useSingleHome } from './SingleHomeContext'

interface LiveHomeValue {
  summary?: HomeSummary
  sensors: SensorSummary[]
  alerts: AlertSummary[]
  services: ElectricService[]
  cycle?: BillingCycleSummary
  configuration?: ConfigurationStatus
  currentAssignment?: CurrentRateAssignment
  loading: boolean
  error: unknown
  refresh: () => Promise<void>
}

const LiveHomeContext = createContext<LiveHomeValue | undefined>(undefined)

export function LiveHomeProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient()
  const { resolution } = useSingleHome()
  const homeId = resolution?.state === 'ready' ? resolution.home.id : undefined
  const sensors = useQuery({
    queryKey: ['sensors', homeId],
    queryFn: () => request(`/api/v1/devices?site_id=${encodeURIComponent(homeId ?? '')}`, {}, adaptSensors),
    enabled: Boolean(homeId),
    refetchInterval: 15_000,
  })
  const services = useQuery({
    queryKey: ['electric-services', homeId],
    queryFn: () => request('/api/v1/utility-accounts', {}, adaptElectricServices),
    enabled: Boolean(homeId),
    select: (items) => items.filter((item) => item.homeId === homeId && item.status === 'active'),
    refetchInterval: 60_000,
  })
  const configuration = useQuery({
    queryKey: ['configuration-status', homeId],
    queryFn: () => request(
      `/api/v1/configuration-status?site_id=${encodeURIComponent(homeId ?? '')}`,
      {},
      adaptConfigurationStatus,
    ),
    enabled: Boolean(homeId),
    retry: 1,
    refetchInterval: 60_000,
  })
  const currentAssignment = useQuery({
    queryKey: ['current-rate-assignment', homeId],
    queryFn: () => request(
      `/api/v1/electric-services/default/current-rate-assignment?site_id=${encodeURIComponent(homeId ?? '')}`,
      {},
      adaptCurrentRateAssignment,
    ),
    enabled: Boolean(homeId),
    retry: 1,
    refetchInterval: 60_000,
  })
  const activeService = services.data?.[0]
  const cycle = useQuery({
    queryKey: ['billing-cycle-summary', activeService?.id],
    queryFn: () => request(`/api/v1/utility-accounts/${activeService?.id ?? ''}/tier-status`, {}, adaptBillingCycle),
    enabled: Boolean(activeService?.id),
    retry: 1,
    refetchInterval: 60_000,
  })
  const fleet = useQuery({
    queryKey: ['home-summary', homeId],
    queryFn: () => request<unknown>(`/api/v1/fleet/summary?site_id=${encodeURIComponent(homeId ?? '')}`),
    enabled: Boolean(homeId),
    refetchInterval: 15_000,
  })
  const alerts = useQuery({
    queryKey: ['alerts', 'active'],
    queryFn: () => request('/api/v1/alerts?status=active', {}, adaptAlerts),
    enabled: Boolean(homeId),
    refetchInterval: 30_000,
  })

  useEffect(() => {
    if (!homeId || typeof EventSource === 'undefined') return
    const source = new EventSource(`/api/v1/events/stream?site_id=${encodeURIComponent(homeId)}`)
    const refreshLive = () => {
      void client.invalidateQueries({ queryKey: ['home-summary', homeId] })
      void client.invalidateQueries({ queryKey: ['sensors', homeId] })
      void client.invalidateQueries({ queryKey: ['history'] })
    }
    source.addEventListener('heartbeat', refreshLive)
    source.addEventListener('reading', refreshLive)
    source.addEventListener('device_status', refreshLive)
    source.addEventListener('fleet', refreshLive)
    source.addEventListener('alert', () => {
      void client.invalidateQueries({ queryKey: ['alerts'] })
    })
    source.onerror = () => {
      // Polling remains active when the browser or reverse proxy closes SSE.
    }
    return () => {
      source.close()
    }
  }, [client, homeId])

  const summary = useMemo(
    () => fleet.data
      ? adaptHomeSummary(fleet.data, sensors.data ?? [], activeService, cycle.data)
      : undefined,
    [activeService, cycle.data, fleet.data, sensors.data],
  )
  useEffect(() => {
    if (import.meta.env.VITE_LIVE_PIPELINE_DEBUG !== 'true' || !homeId || !summary) return
    console.debug('[power-monitor:live-home]', {
      homeId,
      sensorCount: sensors.data?.length ?? 0,
      reportingCount: summary.reportingSensors,
      fleetResponseTimestamp: summary.latestDataAt,
      sensorMeasurementTimestamps: (sensors.data ?? []).map((sensor) => ({
        sensorId: sensor.id,
        latestMeasurementAt: sensor.latestMeasurementAt,
        freshness: sensor.measurementFreshness,
      })),
      queryRefreshTime: new Date().toISOString(),
    })
  }, [homeId, sensors.data, summary])
  const error = fleet.error ?? sensors.error ?? services.error ?? alerts.error
  const value: LiveHomeValue = {
    summary,
    sensors: sensors.data ?? [],
    alerts: alerts.data ?? [],
    services: services.data ?? [],
    cycle: cycle.data,
    configuration: configuration.data,
    currentAssignment: currentAssignment.data,
    loading: fleet.isLoading || sensors.isLoading || services.isLoading,
    error,
    refresh: async () => {
      await Promise.all([
        fleet.refetch(),
        sensors.refetch(),
        services.refetch(),
        alerts.refetch(),
        cycle.refetch(),
        configuration.refetch(),
        currentAssignment.refetch(),
      ])
    },
  }
  return <LiveHomeContext.Provider value={value}>{children}</LiveHomeContext.Provider>
}

export function useLiveHome(): LiveHomeValue {
  const value = useContext(LiveHomeContext)
  if (!value) throw new Error('LiveHomeProvider is missing')
  return value
}
