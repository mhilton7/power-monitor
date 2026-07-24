import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  Archive,
  BadgeDollarSign,
  BatteryCharging,
  Bell,
  CalendarCheck,
  CalendarClock,
  ChartNoAxesCombined,
  ChevronDown,
  CircleDollarSign,
  Clock3,
  Cpu,
  Database,
  Gauge,
  HeartPulse,
  Inbox,
  MapPin,
  PackageCheck,
  Radio,
  RefreshCw,
  ScanLine,
  Send,
  Server,
  ShieldCheck,
  TriangleAlert,
  Unplug,
  Wifi,
  Zap,
  type LucideIcon,
} from 'lucide-react'
import {
  createContext,
  memo,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { useLocation } from 'react-router-dom'
import { api } from '../api'
import type {
  StatusBreakpoint,
  StatusIndicatorValue,
  StatusLayoutItem,
  StatusRegistryResponse,
  StatusResolvedLayout,
  StatusValuesResponse,
} from '../types'

const icons: Record<string, LucideIcon> = {
  activity: Activity,
  archive: Archive,
  'badge-dollar-sign': BadgeDollarSign,
  'battery-charging': BatteryCharging,
  bell: Bell,
  'calendar-check': CalendarCheck,
  'calendar-clock': CalendarClock,
  'chart-no-axes-combined': ChartNoAxesCombined,
  'circle-dollar-sign': CircleDollarSign,
  'clock-3': Clock3,
  cpu: Cpu,
  database: Database,
  gauge: Gauge,
  'heart-pulse': HeartPulse,
  inbox: Inbox,
  'map-pin': MapPin,
  'package-check': PackageCheck,
  radio: Radio,
  'refresh-cw': RefreshCw,
  'scan-line': ScanLine,
  send: Send,
  server: Server,
  'shield-check': ShieldCheck,
  'triangle-alert': TriangleAlert,
  unplug: Unplug,
  wifi: Wifi,
  zap: Zap,
}

interface StatusContextValue {
  breakpoint: StatusBreakpoint
  page: string
  layout?: StatusResolvedLayout
  values: Record<string, StatusIndicatorValue>
}

const StatusContext = createContext<StatusContextValue>({
  breakpoint: 'desktop',
  page: 'overview',
  values: {},
})

export function pageFromPath(pathname: string): string {
  if (pathname === '/' || pathname.startsWith('/overview')) return 'overview'
  if (/^\/(monitoring\/)?devices\/[^/]+/.test(pathname)) return 'device_detail'
  if (pathname.startsWith('/monitoring/devices') || pathname.startsWith('/devices')) return 'devices'
  if (pathname.startsWith('/monitoring/topology') || pathname.startsWith('/topology')) return 'topology'
  if (pathname.startsWith('/analytics/usage') || pathname.startsWith('/usage')) return 'usage'
  if (pathname.startsWith('/analytics/history') || pathname.startsWith('/history')) return 'history'
  if (pathname.startsWith('/analytics/costs') || pathname.startsWith('/costs')) return 'costs'
  if (pathname.startsWith('/billing/rate-sources') || pathname.startsWith('/rates/sources')) return 'rate_sources'
  if (pathname.startsWith('/billing') || pathname.startsWith('/rates')) return 'rates'
  if (pathname.startsWith('/alerts')) return 'alerts'
  if (pathname.startsWith('/monitoring/enrollment') || pathname.startsWith('/enrollment')) return 'enrollment'
  if (pathname.startsWith('/reports')) return 'backups'
  if (
    pathname.startsWith('/administration/security')
    || pathname.startsWith('/administration/system-health')
  ) return 'system_health'
  return 'administration'
}

function viewportBreakpoint(): StatusBreakpoint {
  if (window.matchMedia('(max-width: 640px)').matches) return 'mobile'
  if (window.matchMedia('(max-width: 900px)').matches) return 'tablet'
  return 'desktop'
}

function useBreakpoint(): StatusBreakpoint {
  const [breakpoint, setBreakpoint] = useState<StatusBreakpoint>(viewportBreakpoint)
  useEffect(() => {
    const mobile = window.matchMedia('(max-width: 640px)')
    const tablet = window.matchMedia('(max-width: 900px)')
    const update = () => { setBreakpoint(mobile.matches ? 'mobile' : tablet.matches ? 'tablet' : 'desktop') }
    mobile.addEventListener('change', update)
    tablet.addEventListener('change', update)
    return () => {
      mobile.removeEventListener('change', update)
      tablet.removeEventListener('change', update)
    }
  }, [])
  return breakpoint
}

export function StatusIndicatorProvider({ children }: { children: ReactNode }) {
  const location = useLocation()
  const breakpoint = useBreakpoint()
  const page = pageFromPath(location.pathname)
  const deviceId = page === 'device_detail' ? location.pathname.split('/')[2] : undefined
  const [siteId, setSiteId] = useState(() => localStorage.getItem('pm-site-id') ?? undefined)
  useEffect(() => {
    const updateSite = (event: Event) => {
      setSiteId((event as CustomEvent<string>).detail || undefined)
    }
    window.addEventListener('pm-site-scope-changed', updateSite)
    return () => { window.removeEventListener('pm-site-scope-changed', updateSite) }
  }, [])
  useQuery({
    queryKey: ['status-indicators', 'registry'],
    queryFn: () => api<StatusRegistryResponse>('/api/v1/status-indicators/registry'),
    staleTime: 5 * 60_000,
    retry: 1,
  })
  const layout = useQuery({
    queryKey: ['status-indicators', 'layout', page, breakpoint],
    queryFn: () => api<StatusResolvedLayout>(`/api/v1/status-indicators/layout?page=${encodeURIComponent(page)}&breakpoint=${breakpoint}`),
    staleTime: 30_000,
    refetchInterval: 30_000,
    retry: 1,
  })
  const visibleKeys = useMemo(
    () => layout.data?.zones.flatMap((zone) => zone.items.map((item) => item.indicator_key)) ?? [],
    [layout.data],
  )
  const values = useQuery({
    queryKey: ['status-indicators', 'values', visibleKeys.join(','), siteId, deviceId],
    queryFn: () => {
      const query = new URLSearchParams({ keys: visibleKeys.join(',') })
      if (siteId) query.set('site_id', siteId)
      if (deviceId) query.set('device_id', deviceId)
      return api<StatusValuesResponse>(`/api/v1/status-indicators/values?${query.toString()}`)
    },
    enabled: visibleKeys.length > 0,
    staleTime: 10_000,
    refetchInterval: 15_000,
    retry: 1,
  })
  const context = useMemo<StatusContextValue>(() => ({
    breakpoint,
    page,
    layout: layout.data,
    values: values.data?.values ?? {},
  }), [breakpoint, layout.data, page, values.data?.values])
  return <StatusContext.Provider value={context}>{children}</StatusContext.Provider>
}

export function useStatusIndicators(): StatusContextValue {
  return useContext(StatusContext)
}

function itemAriaLabel(item: StatusLayoutItem, value?: StatusIndicatorValue): string {
  const label = item.definition?.default_label ?? item.indicator_key
  return value?.display_value ? `${label}: ${value.display_value}` : `${label}: status unavailable`
}

function formatFreshness(value?: string): string {
  if (!value) return 'Unavailable'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export const StatusIndicatorItem = memo(function StatusIndicatorItem({
  item,
  value,
}: {
  item: StatusLayoutItem
  value?: StatusIndicatorValue
}) {
  const definition = item.definition
  if (!definition) return null
  const Icon = icons[definition.icon] ?? Activity
  const density = item.density ?? 'standard'
  const showLabel = item.show_label !== false
  const showValue = item.show_value !== false
  const showFreshness = item.show_freshness !== false && Boolean(value?.freshness_at)
  const tooltip = item.show_tooltip === false ? undefined : [definition.description, value?.detail].filter(Boolean).join(' ')
  return (
    <article
      className={`status-indicator status-density-${density} severity-${item.show_severity === false ? 'neutral' : value?.severity ?? 'unknown'}`}
      aria-label={itemAriaLabel(item, value)}
      data-indicator-key={definition.key}
      data-metric-identity={definition.metric_identity}
      data-renderer={definition.renderer}
      title={tooltip}
    >
      {item.show_icon !== false && <span className="status-indicator-icon" aria-hidden="true"><Icon /></span>}
      <span className="status-indicator-copy">
        {showLabel && <span className="status-indicator-label">{definition.default_label}</span>}
        {showValue && <strong className="status-indicator-value">{value?.display_value ?? 'Unavailable'}</strong>}
        {density === 'detailed' && value?.detail && <small>{value.detail}</small>}
        {showFreshness && <small>Updated {formatFreshness(value?.freshness_at)}</small>}
      </span>
      <span className="status-severity-text">{value?.severity ?? 'unknown'}</span>
    </article>
  )
})

export function StatusIndicatorZone({
  zone,
  className = '',
  layout,
  values,
}: {
  zone: string
  className?: string
  layout?: StatusResolvedLayout
  values?: Record<string, StatusIndicatorValue>
}) {
  const context = useStatusIndicators()
  const activeLayout = layout ?? context.layout
  const activeValues = values ?? context.values
  const items = activeLayout?.zones.find((candidate) => candidate.key === zone)?.items ?? []
  if (items.length === 0) return null
  return (
    <section className={`status-indicator-zone status-zone-${zone} ${className}`.trim()} data-status-zone={zone} aria-label={zone.replaceAll('_', ' ')}>
      {items.map((item) => <StatusIndicatorItem key={`${item.indicator_key}-${item.zone}`} item={item} value={activeValues[item.indicator_key]} />)}
    </section>
  )
}

export function MobileStatusDrawer() {
  const { layout } = useStatusIndicators()
  const [open, setOpen] = useState(false)
  const count = layout?.zones.find((zone) => zone.key === 'mobile_status_drawer')?.items.length ?? 0
  if (!count) return null
  return (
    <aside className="mobile-status-drawer">
      <button type="button" className="button secondary" aria-expanded={open} onClick={() => { setOpen((value) => !value) }}>
        More status <span>{count}</span><ChevronDown size={15} aria-hidden="true" />
      </button>
      {open && <StatusIndicatorZone zone="mobile_status_drawer" />}
    </aside>
  )
}
