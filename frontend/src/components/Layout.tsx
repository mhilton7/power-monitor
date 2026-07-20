import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  Bell,
  ChevronDown,
  CircuitBoard,
  History,
  House,
  LogOut,
  Menu,
  Moon,
  RadioTower,
  ScanLine,
  Settings,
  SlidersHorizontal,
  Sun,
  UserRoundCog,
  X,
  Zap,
} from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { sessionPermissions } from '../access'
import { api } from '../api'
import { useInterfaceText } from '../interfaceText'
import type { FleetSummary, Session, Site } from '../types'
import { formatNumber } from './UI'

const navigation = [
  { to: '/', key: 'navigation.overview', fallback: 'Overview', icon: House, permission: 'overview.view' },
  { to: '/devices', key: 'navigation.devices', fallback: 'Devices', icon: RadioTower, permission: 'devices.view' },
  { to: '/topology', key: 'navigation.topology', fallback: 'Topology', icon: CircuitBoard, permission: 'topology.view' },
  { to: '/history', key: 'navigation.history', fallback: 'History', icon: History, permission: 'history.view' },
  { to: '/rates', key: 'navigation.rates', fallback: 'Rates', icon: Activity, permission: 'rates.view' },
  { to: '/alerts', key: 'navigation.alerts', fallback: 'Alerts & Notifications', icon: Bell, permission: 'alerts.view' },
  { to: '/enrollment', key: 'navigation.enrollment', fallback: 'Enrollment', icon: ScanLine, permission: 'enrollment.view' },
  { to: '/admin', key: 'navigation.administration', fallback: 'Administration', icon: Settings, permission: 'settings.view' },
  { to: '/administration/users-access', key: 'navigation.users_access', fallback: 'Users & Access', icon: UserRoundCog, permission: 'users.view', nested: true },
  { to: '/administration/interface-text', key: 'navigation.interface_text', fallback: 'Dashboard & Login Text', icon: SlidersHorizontal, permission: 'interface_text.view', nested: true },
]

export function Layout({ session, children }: { session: Session; children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [theme, setTheme] = useState(() => localStorage.getItem('pm-theme') ?? 'light')
  const [siteId, setSiteId] = useState<string>()
  const pointerSelectedControl = useRef<HTMLSelectElement | null>(null)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { text } = useInterfaceText()
  const permissions = sessionPermissions(session)
  const sites = useQuery({ queryKey: ['sites'], queryFn: () => api<Site[]>('/api/v1/sites'), enabled: permissions.has('sites.view') })
  const summary = useQuery({
    queryKey: ['fleet', siteId],
    queryFn: () => api<FleetSummary>(`/api/v1/fleet/summary${siteId ? `?site_id=${siteId}` : ''}`),
    enabled: permissions.has('overview.view'),
  })
  const logout = useMutation({
    mutationFn: () => api<void>('/api/v1/auth/logout', { method: 'POST' }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['session'] })
      await navigate('/sign-in')
    },
  })
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('pm-theme', theme)
  }, [theme])
  useEffect(() => {
    if (!siteId && sites.data?.[0]) setSiteId(sites.data[0].id)
  }, [siteId, sites.data])
  const fleetHealthy = Boolean(summary.data?.total_devices) && summary.data?.online_devices === summary.data?.total_devices

  return (
    <div
      className="app-shell"
      onPointerDownCapture={(event) => {
        pointerSelectedControl.current = event.target instanceof HTMLSelectElement ? event.target : null
      }}
      onChangeCapture={(event) => {
        if (!(event.target instanceof HTMLSelectElement) || pointerSelectedControl.current !== event.target) return
        const control = event.target
        requestAnimationFrame(() => {
          if (pointerSelectedControl.current !== control) return
          control.blur()
          pointerSelectedControl.current = null
        })
      }}
    >
      <a href="#main" className="skip-link">Skip to content</a>
      <aside className={`sidebar ${menuOpen ? 'sidebar-open' : ''}`}>
        <div className="brand">
          <span className="brand-mark"><Zap size={22} fill="currentColor" /></span>
          <div>
            <strong title={text('general.application_name')}>{text('general.application_short_name')}</strong>
            <small title={text('general.organization_tagline')}>{text('general.organization_tagline')}</small>
          </div>
          <button className="icon-button sidebar-close" onClick={() => { setMenuOpen(false) }} aria-label="Close navigation"><X /></button>
        </div>
        <nav aria-label="Primary">
          {navigation
            .filter((item) => permissions.has(item.permission))
            .map(({ to, key, fallback, icon: Icon, nested }) => (
              <NavLink key={to} className={nested ? 'nav-nested' : undefined} to={to} end={to === '/'} onClick={() => { setMenuOpen(false) }} title={text(key, fallback)}>
                <Icon size={19} aria-hidden="true" />
                <span>{text(key, fallback)}</span>
              </NavLink>
            ))}
        </nav>
      </aside>
      {menuOpen && <button className="nav-scrim" aria-label="Close navigation" onClick={() => { setMenuOpen(false) }} />}
      <div className="app-content">
        <header className="topbar">
          <button className="icon-button menu-button" onClick={() => { setMenuOpen(true) }} aria-label="Open navigation"><Menu /></button>
          {permissions.has('sites.view') && <label className="site-select">
            <span>Viewing</span>
            <select value={siteId ?? ''} onChange={(event) => { setSiteId(event.target.value) }}>
              {sites.data?.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}
            </select>
            <ChevronDown size={15} aria-hidden="true" />
          </label>}
          {permissions.has('overview.view') && <div className="topbar-live" aria-label="Current fleet health and aggregate load">
            <span className="live-pulse" />
            <span>{fleetHealthy ? 'Healthy' : 'Needs attention'}</span>
            <small>· live</small>
            <strong>{formatNumber(summary.data?.current_load_w)} W</strong>
          </div>}
          <div className="topbar-actions">
            <button className="icon-button" onClick={() => { setTheme(theme === 'dark' ? 'light' : 'dark') }} aria-label="Toggle color theme">
              {theme === 'dark' ? <Sun /> : <Moon />}
            </button>
            {permissions.has('alerts.view') && <NavLink className="alert-button" to="/alerts" aria-label={`${summary.data?.active_alerts ?? 0} active alerts`}>
              <Bell size={19} />
              {(summary.data?.active_alerts ?? 0) > 0 && <span>{summary.data?.active_alerts}</span>}
            </NavLink>}
            <div className="user-menu">
              <span>{session.user?.display_name.slice(0, 1).toUpperCase()}</span>
              <div>
                <strong>{session.user?.display_name}</strong>
                <small>{session.user?.roles.join(' · ')}</small>
              </div>
              <button onClick={() => { logout.mutate() }} disabled={logout.isPending} aria-label="Sign out"><LogOut size={16} /></button>
            </div>
          </div>
        </header>
        {text('footer.banner') && <aside className="dashboard-banner" role="status">{text('footer.banner')}</aside>}
        <main id="main">{children}</main>
        {(text('footer.dashboard') || text('footer.support_label') || text('footer.copyright')) && (
          <footer className="dashboard-footer">
            <span>{text('footer.dashboard')}</span>
            {text('footer.support_url') && text('footer.support_label') && <a href={text('footer.support_url')} rel="noreferrer">{text('footer.support_label')}</a>}
            <span>{text('footer.copyright')}</span>
          </footer>
        )}
      </div>
    </div>
  )
}
