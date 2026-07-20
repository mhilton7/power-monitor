import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  Bell,
  Boxes,
  ChevronDown,
  CircuitBoard,
  FileBarChart,
  Gauge,
  History,
  Menu,
  Moon,
  PackageCheck,
  RadioTower,
  Settings,
  ShieldCheck,
  Sun,
  WalletCards,
  X,
  Zap,
} from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { api } from '../api'
import type { FleetSummary, Session, Site } from '../types'
import { formatNumber, StatusPill } from './UI'

const navigation = [
  { to: '/', label: 'Fleet', icon: Gauge },
  { to: '/devices', label: 'Devices', icon: RadioTower },
  { to: '/topology', label: 'Topology', icon: CircuitBoard },
  { to: '/history', label: 'History', icon: History },
  { to: '/costs', label: 'Costs & billing', icon: WalletCards },
  { to: '/rates', label: 'Rate plans', icon: Activity },
  { to: '/alerts', label: 'Alerts', icon: Bell },
  { to: '/enrollment', label: 'Enrollment', icon: Boxes, operator: true },
  { to: '/firmware', label: 'Firmware', icon: PackageCheck, operator: true },
  { to: '/reports', label: 'Reports', icon: FileBarChart },
  { to: '/admin', label: 'Administration', icon: Settings, admin: true },
]

export function Layout({ session, children }: { session: Session; children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [theme, setTheme] = useState(() => localStorage.getItem('pm-theme') ?? 'dark')
  const [siteId, setSiteId] = useState<string>()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const sites = useQuery({ queryKey: ['sites'], queryFn: () => api<Site[]>('/api/v1/sites') })
  const summary = useQuery({
    queryKey: ['fleet', siteId],
    queryFn: () => api<FleetSummary>(`/api/v1/fleet/summary${siteId ? `?site_id=${siteId}` : ''}`),
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
  const roles = new Set(session.user?.roles ?? [])

  return (
    <div className="app-shell">
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <aside className={`sidebar ${menuOpen ? 'sidebar-open' : ''}`}>
        <div className="brand">
          <span className="brand-mark">
            <Zap size={22} fill="currentColor" />
          </span>
          <div>
            <strong>Power Monitor</strong>
            <small>Fleet control</small>
          </div>
          <button className="icon-button sidebar-close" onClick={() => { setMenuOpen(false); }} aria-label="Close navigation">
            <X />
          </button>
        </div>
        <nav aria-label="Primary">
          {navigation
            .filter((item) => !item.admin || roles.has('admin'))
            .filter((item) => !item.operator || roles.has('admin') || roles.has('operator'))
            .map(({ to, label, icon: Icon }) => (
              <NavLink key={to} to={to} end={to === '/'} onClick={() => { setMenuOpen(false); }}>
                <Icon size={19} aria-hidden="true" />
                <span>{label}</span>
              </NavLink>
            ))}
        </nav>
        <div className="sidebar-security">
          <ShieldCheck size={18} />
          <div>
            <strong>Private by design</strong>
            <small>Same-origin · No cloud dependency</small>
          </div>
        </div>
      </aside>
      {menuOpen && <button className="nav-scrim" aria-label="Close navigation" onClick={() => { setMenuOpen(false); }} />}
      <div className="app-content">
        <header className="topbar">
          <button className="icon-button menu-button" onClick={() => { setMenuOpen(true); }} aria-label="Open navigation">
            <Menu />
          </button>
          <label className="site-select">
            <span>Site</span>
            <select value={siteId ?? ''} onChange={(event) => { setSiteId(event.target.value); }}>
              {sites.data?.map((site) => (
                <option key={site.id} value={site.id}>
                  {site.name}
                </option>
              ))}
            </select>
            <ChevronDown size={15} aria-hidden="true" />
          </label>
          <div className="topbar-live" aria-label="Current aggregate load">
            <span className="live-pulse" />
            <span>Live load</span>
            <strong>{formatNumber(summary.data?.current_load_w)} W</strong>
          </div>
          <div className="topbar-actions">
            <button className="icon-button" onClick={() => { setTheme(theme === 'dark' ? 'light' : 'dark'); }} aria-label="Toggle color theme">
              {theme === 'dark' ? <Sun /> : <Moon />}
            </button>
            <NavLink className="alert-button" to="/alerts" aria-label={`${summary.data?.active_alerts ?? 0} active alerts`}>
              <Bell size={19} />
              {(summary.data?.active_alerts ?? 0) > 0 && <span>{summary.data?.active_alerts}</span>}
            </NavLink>
            <div className="user-menu">
              <span>{session.user?.display_name.slice(0, 1).toUpperCase()}</span>
              <div>
                <strong>{session.user?.display_name}</strong>
                <small>{session.user?.roles.join(' · ')}</small>
              </div>
              <button onClick={() => { logout.mutate(); }} disabled={logout.isPending}>
                Sign out
              </button>
            </div>
          </div>
        </header>
        <main id="main">{children}</main>
        <footer>
          <span>Power Monitor Server 1.0.0</span>
          <StatusPill status="healthy" label="Server protected" />
          <span>pm-protocol/1.0.0</span>
        </footer>
      </div>
    </div>
  )
}
