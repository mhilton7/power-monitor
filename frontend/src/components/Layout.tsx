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
  PanelsTopLeft,
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
import type { Session, Site } from '../types'
import { MobileStatusDrawer, StatusIndicatorZone } from './StatusIndicators'

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
  { to: '/administration/status-indicators', key: 'navigation.status_indicators', fallback: 'Status Indicators & Layout', icon: PanelsTopLeft, permission: 'status_indicators.view', nested: true },
]

export function Layout({ session, children }: { session: Session; children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [theme, setTheme] = useState(() => localStorage.getItem('pm-theme') ?? 'light')
  const [siteId, setSiteId] = useState<string | undefined>(() => localStorage.getItem('pm-site-id') ?? undefined)
  const pointerSelectedControl = useRef<HTMLSelectElement | null>(null)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { text } = useInterfaceText()
  const permissions = sessionPermissions(session)
  const sites = useQuery({ queryKey: ['sites'], queryFn: () => api<Site[]>('/api/v1/sites'), enabled: permissions.has('sites.view') })
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
    const firstSite = sites.data?.[0]
    if (!firstSite) return
    const nextSiteId = sites.data?.some((site) => site.id === siteId) ? siteId : firstSite.id
    if (nextSiteId !== siteId) setSiteId(nextSiteId)
  }, [siteId, sites.data])
  useEffect(() => {
    if (!siteId) return
    localStorage.setItem('pm-site-id', siteId)
    window.dispatchEvent(new CustomEvent<string>('pm-site-scope-changed', { detail: siteId }))
  }, [siteId])
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
        <StatusIndicatorZone zone="sidebar_upper" />
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
        <StatusIndicatorZone zone="sidebar_lower" />
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
          <StatusIndicatorZone zone="global_header_left" className="topbar-status-zone" />
          <StatusIndicatorZone zone="global_header_center" className="topbar-status-zone" />
          <div className="topbar-actions">
            <StatusIndicatorZone zone="global_header_right" className="topbar-status-zone" />
            <button className="icon-button" onClick={() => { setTheme(theme === 'dark' ? 'light' : 'dark') }} aria-label="Toggle color theme">
              {theme === 'dark' ? <Sun /> : <Moon />}
            </button>
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
        <StatusIndicatorZone zone="mobile_header" />
        <StatusIndicatorZone zone="global_status_row" />
        <StatusIndicatorZone zone="mobile_status_strip" />
        <MobileStatusDrawer />
        {text('footer.banner') && <aside className="dashboard-banner" role="status">{text('footer.banner')}</aside>}
        <main id="main">{children}<StatusIndicatorZone zone="page_footer" /></main>
        <StatusIndicatorZone zone="global_footer" />
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
