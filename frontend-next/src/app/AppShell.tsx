import {
  Bell,
  ChevronLeft,
  ChevronRight,
  CircleUserRound,
  Clock3,
  CreditCard,
  Gauge,
  History,
  Home,
  FlaskConical,
  LogOut,
  Menu,
  Settings,
  Zap,
} from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { NavLink, useLocation, useNavigate } from './router'
import { json, request } from '../api/client'
import { useAppearance } from '../state/AppearanceContext'
import { useAuth } from '../state/AuthContext'
import { useLiveHome } from '../state/LiveHomeContext'
import { useSingleHome } from '../state/SingleHomeContext'
import { power, relativeTime } from '../utils/format'
import { AlertDrawer } from '../features/alerts/AlertDrawer'
import { StatusDot } from '../components/data-display/Surface'
import { DropdownMenu, DropdownMenuItem } from '../components/overlays/DropdownMenu'
import { ConfigurationStatusChip } from '../features/configuration/ConfigurationStatusSurface'
import { useTestMode } from '../state/TestModeContext'

export const PRIMARY_DESTINATIONS = [
  { label: 'Home', path: '/home', icon: Home },
  { label: 'History', path: '/history', icon: History },
  { label: 'Billing', path: '/billing', icon: CreditCard },
  { label: 'Settings', path: '/settings', icon: Settings },
] as const

export function AppShell({ children }: { children: ReactNode }) {
  const { resolution } = useSingleHome()
  const { summary, alerts, configuration } = useLiveHome()
  const { session, refresh } = useAuth()
  const { railCollapsed, setRailCollapsed } = useAppearance()
  const testMode = useTestMode()
  const location = useLocation()
  const [alertsOpen, setAlertsOpen] = useState(new URLSearchParams(location.search).get('alerts') === '1')
  const navigate = useNavigate()
  const home = resolution?.state === 'ready' ? resolution.home : undefined
  const liveState = summary?.hasLiveData ? 'live' : summary?.totalSensors ? 'waiting' : 'attention'

  const logout = async () => {
    await request('/api/v1/auth/logout', json('POST'))
    await refresh()
    navigate('/sign-in', { replace: true })
  }

  return (
    <div className={`app-shell ${railCollapsed ? 'rail-collapsed' : ''}`}>
      <aside className="side-rail">
        <div className="brand">
          <span className="brand-mark"><Zap fill="currentColor" aria-hidden="true" /></span>
          {!railCollapsed && <div><strong>Power Monitor</strong><small>Local energy intelligence</small></div>}
        </div>
        <nav aria-label="Primary">
          {PRIMARY_DESTINATIONS.map(({ label, path, icon: Icon }) => (
            <NavLink
              key={path}
              to={path}
              className={({ isActive }) => isActive || location.pathname.startsWith(`${path}/`) ? 'active' : undefined}
              aria-label={railCollapsed ? label : undefined}
              title={railCollapsed ? label : undefined}
            >
              <Icon aria-hidden="true" />
              {!railCollapsed && <span>{label}</span>}
            </NavLink>
          ))}
        </nav>
        <button
          type="button"
          className="rail-toggle"
          onClick={() => { setRailCollapsed(!railCollapsed); }}
          aria-label={railCollapsed ? 'Expand navigation' : 'Collapse navigation'}
        >
          {railCollapsed ? <ChevronRight /> : <ChevronLeft />}
          {!railCollapsed && <span>Collapse</span>}
        </button>
      </aside>

      <header className="top-bar">
        <div className="home-identity">
          <span className="icon-tile"><Home aria-hidden="true" /></span>
          <div><small>Home</small><strong>{home?.name ?? 'Your home'}</strong></div>
        </div>
        <div className="live-facts" aria-label="Current home status">
          <div><Gauge aria-hidden="true" /><span><small>Live data</small><StatusDot state={liveState} label={summary?.hasLiveData ? 'Live' : 'Waiting'} /></span></div>
          <div><Zap aria-hidden="true" /><span><small>Current load</small><strong>{power(summary?.currentPowerW)}</strong></span></div>
          <div className="freshness"><Clock3 aria-hidden="true" /><span><small>Last data</small><strong>{relativeTime(summary?.latestDataAt)}</strong></span></div>
        </div>
        <div className="top-actions">
          <ConfigurationStatusChip status={configuration} className="top-configuration-status" />
          <button
            type="button"
            className="icon-button alert-button"
            aria-label={`${alerts.length} active alerts`}
            aria-expanded={alertsOpen}
            onClick={() => { setAlertsOpen(true); }}
          >
            <Bell />
            {alerts.length > 0 && <span>{alerts.length}</span>}
          </button>
          <div className="user-menu">
            <DropdownMenu
              label="Account menu"
              triggerClassName="user-button"
              menuClassName="user-popover"
              trigger={<>
                <CircleUserRound aria-hidden="true" />
                <span><strong>{session?.user?.name ?? 'User'}</strong><small>{session?.user?.roles.includes('admin') ? 'Owner' : 'Family'}</small></span>
                <Menu aria-hidden="true" />
              </>}
            >
              <span>{session?.user?.email}</span>
              <DropdownMenuItem onSelect={() => { void logout() }}><LogOut size={16} /> Sign out</DropdownMenuItem>
            </DropdownMenu>
          </div>
        </div>
      </header>

      <main id="main-content" className="page-content" tabIndex={-1}>
        {testMode.state?.enabled && (
          <section className="test-mode-banner" role="status" aria-label="Sensor Test Mode is active">
            <FlaskConical aria-hidden="true" />
            <div>
              <strong>Sensor Test Mode</strong>
              <span>
                Test Mode is active. Showing {testMode.state.onlineSensors}/{testMode.state.sensorCount} simulated sensors
                {testMode.state.paused ? ' · paused' : ''}
                {' · '}{testMode.state.expiresAt ? `expires in ${Math.max(1, Math.ceil(testMode.state.remainingSeconds / 60))} min` : 'runs until disabled'}
                {' · '}real readings and billing records are not being changed
              </span>
            </div>
            <NavLink className="button secondary compact" to="/settings/advanced/sensor-test-mode">Manage</NavLink>
            <button
              className="button secondary compact"
              type="button"
              disabled={testMode.changing}
              onClick={() => { void testMode.disable() }}
            >
              Exit test mode
            </button>
          </section>
        )}
        {children}
      </main>

      <nav className="mobile-nav" aria-label="Primary mobile">
        {PRIMARY_DESTINATIONS.map(({ label, path, icon: Icon }) => (
          <NavLink key={path} to={path}>
            <Icon aria-hidden="true" />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
      <AlertDrawer open={alertsOpen} alerts={alerts} onClose={() => { setAlertsOpen(false); }} />
    </div>
  )
}
