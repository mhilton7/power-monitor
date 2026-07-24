import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BarChart3,
  Bell,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CreditCard,
  House,
  LogOut,
  Menu,
  Moon,
  RadioTower,
  Settings,
  Sun,
  X,
  Zap,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { sessionPermissions } from '../access'
import { api } from '../api'
import { useInterfaceText } from '../interfaceText'
import type { Session, Site } from '../types'
import { canOpenWorkspace, WORKSPACES, type WorkspaceId } from '../workspaces'
import { MobileStatusDrawer, StatusIndicatorZone } from './StatusIndicators'

const workspaceIcons = {
  overview: House,
  monitoring: RadioTower,
  analytics: BarChart3,
  billing: CreditCard,
  alerts: Bell,
  administration: Settings,
} satisfies Record<WorkspaceId, typeof House>

function chooseSafeSite(sites: Site[], requested?: string): string | undefined {
  const active = sites.filter((site) => !site.lifecycle_state || site.lifecycle_state === 'active')
  const requestedSite = active.find((site) => site.id === requested)
  return requestedSite?.id ?? active.find((site) => site.is_default)?.id ?? active[0]?.id
}

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => (
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(query).matches
      : false
  ))

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const media = window.matchMedia(query)
    const update = () => { setMatches(media.matches) }
    update()
    media.addEventListener('change', update)
    return () => { media.removeEventListener('change', update) }
  }, [query])

  return matches
}

export function filterSelectableSites(sites: Site[], selectedSiteId: string | undefined, search: string): Site[] {
  const term = search.trim().toLocaleLowerCase()
  if (!term) return sites
  return sites.filter((site) => (
    site.id === selectedSiteId
    || site.name.toLocaleLowerCase().includes(term)
    || site.code?.toLocaleLowerCase().includes(term)
  ))
}

function SiteSwitcher({
  canManage,
  onManage,
  onSearch,
  onSelect,
  search,
  selectedSiteId,
  sites,
  visibleSites,
}: {
  canManage: boolean
  onManage?: () => void
  onSearch: (value: string) => void
  onSelect: (value: string) => void
  search: string
  selectedSiteId?: string
  sites: Site[]
  visibleSites: Site[]
}) {
  return (
    <div className="site-switcher">
      {sites.length > 8 && (
        <label className="site-search">
          <span className="sr-only">Search sites</span>
          <input
            type="search"
            value={search}
            onChange={(event) => { onSearch(event.target.value) }}
            placeholder="Search sites"
            autoComplete="off"
          />
        </label>
      )}
      <label className="site-select">
        <span>Viewing</span>
        <select
          aria-label="Current site"
          value={selectedSiteId ?? ''}
          onChange={(event) => { onSelect(event.target.value) }}
          disabled={!sites.length}
        >
          {!sites.length && <option value="">No active sites</option>}
          {visibleSites.map((site) => <option key={site.id} value={site.id}>{site.name}{site.is_default ? ' · Default' : ''}</option>)}
        </select>
        <ChevronDown size={15} aria-hidden="true" />
      </label>
      {canManage && <NavLink className="manage-sites-link" to="/administration/sites-network" onClick={onManage}>Manage sites</NavLink>}
    </div>
  )
}

export function Layout({ session, children }: { session: Session; children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('pm-sidebar-collapsed') === 'true')
  const [theme, setTheme] = useState(() => localStorage.getItem('pm-theme') ?? 'dark')
  const [siteId, setSiteId] = useState<string | undefined>(() => localStorage.getItem('pm-site-id') ?? undefined)
  const [siteSearch, setSiteSearch] = useState('')
  const pointerSelectedControl = useRef<HTMLSelectElement | null>(null)
  const mobileNavigation = useRef<HTMLElement>(null)
  const menuButton = useRef<HTMLButtonElement>(null)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()
  const mobileViewport = useMediaQuery('(max-width: 640px)')
  const { text } = useInterfaceText()
  const permissions = sessionPermissions(session)
  const workspaces = useMemo(() => WORKSPACES.filter((workspace) => canOpenWorkspace(workspace, session)), [session])
  const sites = useQuery({
    queryKey: ['sites'],
    queryFn: () => api<Site[]>('/api/v1/sites'),
    enabled: permissions.has('sites.view'),
  })
  const selectableSites = useMemo(
    () => (sites.data ?? []).filter((site) => !site.lifecycle_state || site.lifecycle_state === 'active'),
    [sites.data],
  )
  const visibleSites = useMemo(
    () => filterSelectableSites(selectableSites, siteId, siteSearch),
    [selectableSites, siteId, siteSearch],
  )
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
    localStorage.setItem('pm-sidebar-collapsed', String(collapsed))
  }, [collapsed])
  useEffect(() => {
    const safe = chooseSafeSite(sites.data ?? [], siteId)
    if (safe !== siteId) setSiteId(safe)
  }, [siteId, sites.data])
  useEffect(() => {
    if (!siteId) {
      localStorage.removeItem('pm-site-id')
      return
    }
    localStorage.setItem('pm-site-id', siteId)
    window.dispatchEvent(new CustomEvent<string>('pm-site-scope-changed', { detail: siteId }))
  }, [siteId])
  useEffect(() => {
    setMenuOpen(false)
  }, [location.pathname])
  useEffect(() => {
    if (!menuOpen) return
    const root = mobileNavigation.current
    if (!root) return
    const focusable = [...root.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled])')]
    // Focus the non-activating drawer container first. Moving focus directly
    // to Close can let the Enter keyup that opened the drawer close it again.
    root.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenuOpen(false)
        menuButton.current?.focus()
        return
      }
      if (event.key !== 'Tab' || !focusable.length) return
      const first = focusable[0]
      const last = focusable.at(-1)
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last?.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first?.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => { document.removeEventListener('keydown', onKeyDown) }
  }, [menuOpen])

  return (
    <div
      className={`app-shell ${collapsed ? 'sidebar-collapsed' : ''}`}
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
      <aside
        className={`sidebar ${menuOpen ? 'sidebar-open' : ''}`}
        ref={mobileNavigation}
        tabIndex={-1}
        aria-label="Application navigation"
      >
        <div className="brand">
          <span className="brand-mark"><Zap size={22} fill="currentColor" /></span>
          <div className="brand-text">
            <strong title={text('general.application_name')}>{text('general.application_short_name')}</strong>
            <small title={text('general.organization_tagline')}>{text('general.organization_tagline')}</small>
          </div>
          <button className="icon-button sidebar-close" onClick={() => { setMenuOpen(false) }} aria-label="Close navigation"><X /></button>
        </div>
        <nav aria-label="Primary">
          {workspaces.map((workspace) => {
            const Icon = workspaceIcons[workspace.id]
            return (
              <NavLink
                key={workspace.id}
                to={workspace.route}
                className={({ isActive }) => isActive ? 'active' : undefined}
                onClick={() => { setMenuOpen(false) }}
                title={text(workspace.labelKey, workspace.label)}
              >
                <Icon size={20} aria-hidden="true" />
                <span>{text(workspace.labelKey, workspace.label)}</span>
              </NavLink>
            )
          })}
        </nav>
        {mobileViewport && (
          <div className="mobile-drawer-controls">
            {permissions.has('sites.view') && (
              <SiteSwitcher
                canManage={permissions.has('sites.edit')}
                onManage={() => { setMenuOpen(false) }}
                onSearch={setSiteSearch}
                onSelect={(value) => {
                  setSiteId(value)
                  setMenuOpen(false)
                }}
                search={siteSearch}
                selectedSiteId={siteId}
                sites={selectableSites}
                visibleSites={visibleSites}
              />
            )}
            <div className="mobile-drawer-user">
              <span>{session.user?.display_name.slice(0, 1).toUpperCase()}</span>
              <div><strong>{session.user?.display_name}</strong><small>{session.user?.roles.join(' · ')}</small></div>
              <button type="button" className="icon-button" onClick={() => { setTheme(theme === 'dark' ? 'light' : 'dark') }} aria-label="Toggle color theme">
                {theme === 'dark' ? <Sun /> : <Moon />}
              </button>
              <button type="button" className="icon-button" onClick={() => { logout.mutate() }} disabled={logout.isPending} aria-label="Sign out"><LogOut size={17} /></button>
            </div>
          </div>
        )}
        <button
          type="button"
          className="sidebar-collapse"
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          onClick={() => { setCollapsed((value) => !value) }}
        >
          {collapsed ? <ChevronRight size={18} /> : <ChevronLeft size={18} />}
          <span>{collapsed ? 'Expand' : 'Collapse'}</span>
        </button>
      </aside>
      {menuOpen && <button className="nav-scrim" aria-label="Close navigation" onClick={() => { setMenuOpen(false) }} />}
      <div className="app-content">
        <header className="topbar">
          <button ref={menuButton} className="icon-button menu-button" onClick={() => { setMenuOpen(true) }} aria-label="Open navigation"><Menu /></button>
          {permissions.has('sites.view') && !mobileViewport && (
            <SiteSwitcher
              canManage={permissions.has('sites.edit')}
              onSearch={setSiteSearch}
              onSelect={setSiteId}
              search={siteSearch}
              selectedSiteId={siteId}
              sites={selectableSites}
              visibleSites={visibleSites}
            />
          )}
          <StatusIndicatorZone zone="top_bar" className="topbar-status-zone" />
          {!mobileViewport && <div className="topbar-actions">
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
          </div>}
        </header>
        <MobileStatusDrawer />
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
