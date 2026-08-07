import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, useEffect, useMemo, useRef, type ReactNode } from 'react'
import {
  adaptNotificationPage,
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
import { useAuth } from './AuthContext'
import { hasAnyPermission, hasPermission } from '../access/permissions'
import { countCurrentAttentionNotifications } from '../features/alerts/notificationSelectors'
import { useLocation } from '../app/router'

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
const HISTORY_SSE_COALESCE_MS = 750

interface HistoryReadingEventPayload {
  site_id?: string
  interval_start?: string
  interval_end?: string
  event_watermark?: string
}

function validEpoch(value: unknown): number | undefined {
  if (typeof value !== 'string') return undefined
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : undefined
}

export function historyReadingEventTouchesQuery(
  queryKey: readonly unknown[],
  payload: HistoryReadingEventPayload,
  now = Date.now(),
): boolean {
  const kind = typeof queryKey[1] === 'string' ? queryKey[1] : ''
  const queryStart = validEpoch(kind === 'home-daily' ? queryKey[3] : queryKey[9])
  const queryEnd = validEpoch(kind === 'home-daily' ? queryKey[4] : queryKey[10])
  const eventStart = validEpoch(payload.interval_start)
  const eventEnd = validEpoch(payload.interval_end)
  if (
    queryStart === undefined
    || queryEnd === undefined
    || eventStart === undefined
    || eventEnd === undefined
  ) return true
  if (eventStart < queryEnd && eventEnd > queryStart) return true
  const rolling = kind === 'home-daily'
    || (kind === 'page' && ['today', '7d', '30d'].includes(String(queryKey[3])))
  return rolling && eventEnd > queryStart && eventStart <= now + 60_000
}

export function LiveHomeProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient()
  const historyRefreshTimer = useRef<number | undefined>(undefined)
  const lastHistoryEventWatermark = useRef<number | undefined>(undefined)
  const location = useLocation()
  const onHistoryRoute = location.pathname === '/history'
  const { session } = useAuth()
  const { resolution } = useSingleHome()
  const homeId = resolution?.state === 'ready' ? resolution.home.id : undefined
  const boundary = session?.user ? `${session.user.id}:${session.user.accessRevision}` : 'anonymous'
  const canViewSensors = hasPermission(session, 'devices.view')
  const canViewServices = hasPermission(session, 'utility_accounts.view')
  const canViewRates = hasPermission(session, 'rates.view')
  const canViewCosts = hasAnyPermission(session, ['costs.view', 'usage.view'])
  const canViewAlerts = hasPermission(session, 'alerts.view')
  const canViewOverview = hasPermission(session, 'overview.view')
  const sensors = useQuery({
    queryKey: ['sensors', boundary, homeId],
    queryFn: () => request(`/api/v1/devices?site_id=${encodeURIComponent(homeId ?? '')}`, {}, adaptSensors),
    enabled: Boolean(homeId && canViewSensors),
    refetchInterval: 15_000,
  })
  const services = useQuery({
    queryKey: ['electric-services', boundary, homeId],
    queryFn: () => request('/api/v1/utility-accounts', {}, adaptElectricServices),
    enabled: Boolean(homeId && canViewServices),
    select: (items) => items.filter((item) => item.homeId === homeId && item.status === 'active'),
    refetchInterval: onHistoryRoute ? false : 60_000,
  })
  const configuration = useQuery({
    queryKey: ['configuration-status', boundary, homeId],
    queryFn: () => request(
      `/api/v1/configuration-status?site_id=${encodeURIComponent(homeId ?? '')}`,
      {},
      adaptConfigurationStatus,
    ),
    enabled: Boolean(homeId && canViewOverview),
    retry: 1,
    refetchInterval: onHistoryRoute ? false : 60_000,
  })
  const currentAssignment = useQuery({
    queryKey: ['current-rate-assignment', boundary, homeId],
    queryFn: () => request(
      `/api/v1/electric-services/default/current-rate-assignment?site_id=${encodeURIComponent(homeId ?? '')}`,
      {},
      adaptCurrentRateAssignment,
    ),
    enabled: Boolean(homeId && canViewRates),
    retry: 1,
    refetchInterval: onHistoryRoute ? false : 60_000,
  })
  const activeService = services.data?.[0]
  const cycle = useQuery({
    queryKey: ['billing-cycle-summary', boundary, activeService?.id],
    queryFn: () => request(`/api/v1/utility-accounts/${activeService?.id ?? ''}/tier-status`, {}, adaptBillingCycle),
    enabled: Boolean(activeService?.id && canViewCosts),
    retry: 1,
    refetchInterval: onHistoryRoute ? false : 60_000,
  })
  const fleet = useQuery({
    queryKey: ['home-summary', boundary, homeId],
    queryFn: () => request<unknown>(`/api/v1/fleet/summary?site_id=${encodeURIComponent(homeId ?? '')}`),
    enabled: Boolean(homeId && canViewOverview),
    refetchInterval: 15_000,
  })
  const alerts = useQuery({
    queryKey: ['alerts', boundary, 'active'],
    queryFn: () => request(`/api/v1/notifications?page_size=200&site_id=${encodeURIComponent(homeId ?? '')}`, {}, adaptNotificationPage),
    enabled: Boolean(homeId && canViewAlerts),
    refetchInterval: 30_000,
  })

  useEffect(() => {
    if (!homeId || !canViewOverview || typeof EventSource === 'undefined') return
    lastHistoryEventWatermark.current = undefined
    const source = new EventSource(`/api/v1/events/stream?site_id=${encodeURIComponent(homeId)}`)
    const refreshLive = () => {
      void client.invalidateQueries({ queryKey: ['home-summary', boundary, homeId] })
      void client.invalidateQueries({ queryKey: ['sensors', boundary, homeId] })
    }
    const refreshHistory = (event: Event) => {
      let payload: HistoryReadingEventPayload = {}
      if (event instanceof MessageEvent) {
        try {
          payload = JSON.parse(String(event.data)) as HistoryReadingEventPayload
          if (payload.site_id && payload.site_id !== homeId) return
          const watermark = validEpoch(payload.event_watermark)
          if (
            watermark !== undefined
            && lastHistoryEventWatermark.current !== undefined
            && watermark <= lastHistoryEventWatermark.current
          ) return
          if (watermark !== undefined) lastHistoryEventWatermark.current = watermark
        } catch {
          // The stream is already scoped by URL. A malformed optional payload
          // must not disable the bounded polling fallback.
        }
      }
      refreshLive()
      if (historyRefreshTimer.current !== undefined) {
        window.clearTimeout(historyRefreshTimer.current)
      }
      historyRefreshTimer.current = window.setTimeout(() => {
        historyRefreshTimer.current = undefined
        void client.refetchQueries(
          {
            type: 'active',
            predicate: (query) => query.getObserversCount() > 0
              && query.queryKey[0] === 'history'
              && ['page', 'home-daily'].includes(String(query.queryKey[1]))
              && query.queryKey[2] === homeId
              && historyReadingEventTouchesQuery(query.queryKey, payload),
          },
          { cancelRefetch: false },
        )
      }, HISTORY_SSE_COALESCE_MS)
    }
    source.addEventListener('heartbeat', refreshLive)
    source.addEventListener('reading', refreshHistory)
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
      if (historyRefreshTimer.current !== undefined) {
        window.clearTimeout(historyRefreshTimer.current)
        historyRefreshTimer.current = undefined
      }
    }
  }, [boundary, canViewOverview, client, homeId])

  const summary = useMemo(() => {
    if (!fleet.data) return undefined
    const adapted = adaptHomeSummary(fleet.data, sensors.data ?? [], activeService, cycle.data)
    if (!canViewAlerts || !alerts.data) return adapted
    return {
      ...adapted,
      // Notification dismissal is scoped to the current user. The fleet response
      // intentionally remains a site-level operational count, so user-facing Home
      // surfaces must use the same filtered notification page as the drawer.
      activeAlerts: countCurrentAttentionNotifications(alerts.data.items),
    }
  }, [activeService, alerts.data, canViewAlerts, cycle.data, fleet.data, sensors.data])
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
  const error = fleet.error
  const value: LiveHomeValue = {
    summary,
    sensors: sensors.data ?? [],
    alerts: alerts.data?.items ?? [],
    services: services.data ?? [],
    cycle: cycle.data,
    configuration: configuration.data,
    currentAssignment: currentAssignment.data,
    loading: fleet.isLoading
      || (canViewSensors && sensors.isLoading)
      || (canViewServices && services.isLoading),
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
