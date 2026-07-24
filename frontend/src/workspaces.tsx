import { createContext, useContext, useEffect, type ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { sessionPermissions } from './access'
import { ActionScope } from './actions'
import { StatusIndicatorZone } from './components/StatusIndicators'
import { useInterfaceText } from './interfaceText'
import type { Session } from './types'

export type WorkspaceId =
  | 'overview'
  | 'monitoring'
  | 'analytics'
  | 'billing'
  | 'alerts'
  | 'administration'

const WorkspacePageContext = createContext(false)

export function useWorkspacePage(): boolean {
  return useContext(WorkspacePageContext)
}

export interface WorkspaceTab {
  id: string
  labelKey: string
  label: string
  route: string
  permissions: string[]
}

export interface WorkspaceDefinition {
  id: WorkspaceId
  labelKey: string
  label: string
  titleKey: string
  title: string
  subtitleKey: string
  subtitle: string
  route: string
  tabs: WorkspaceTab[]
  workspacePermissions?: string[]
}

export const WORKSPACES: WorkspaceDefinition[] = [
  {
    id: 'overview',
    labelKey: 'workspace.overview.label',
    label: 'Overview',
    titleKey: 'workspace.overview.title',
    title: 'Overview',
    subtitleKey: 'workspace.overview.subtitle',
    subtitle: 'Current power, energy, cost, and operational context.',
    route: '/overview',
    tabs: [{ id: 'overview', labelKey: 'workspace.overview.label', label: 'Overview', route: '/overview', permissions: ['overview.view'] }],
  },
  {
    id: 'monitoring',
    labelKey: 'workspace.monitoring.label',
    label: 'Monitoring',
    titleKey: 'workspace.monitoring.title',
    title: 'Monitoring',
    subtitleKey: 'workspace.monitoring.subtitle',
    subtitle: 'Manage sensors, physical relationships, and device enrollment.',
    route: '/monitoring',
    tabs: [
      { id: 'devices', labelKey: 'workspace.monitoring.devices', label: 'Devices', route: '/monitoring/devices', permissions: ['devices.view'] },
      { id: 'topology', labelKey: 'workspace.monitoring.topology', label: 'Topology', route: '/monitoring/topology', permissions: ['topology.view'] },
      { id: 'enrollment', labelKey: 'workspace.monitoring.enrollment', label: 'Enrollment', route: '/monitoring/enrollment', permissions: ['enrollment.view'] },
    ],
  },
  {
    id: 'analytics',
    labelKey: 'workspace.analytics.label',
    label: 'Analytics',
    titleKey: 'workspace.analytics.title',
    title: 'Analytics',
    subtitleKey: 'workspace.analytics.subtitle',
    subtitle: 'Explore usage, historical measurements, and energy costs.',
    route: '/analytics',
    tabs: [
      { id: 'usage', labelKey: 'workspace.analytics.usage', label: 'Usage', route: '/analytics/usage', permissions: ['usage.view'] },
      { id: 'history', labelKey: 'workspace.analytics.history', label: 'History', route: '/analytics/history', permissions: ['history.view'] },
      { id: 'costs', labelKey: 'workspace.analytics.costs', label: 'Costs', route: '/analytics/costs', permissions: ['costs.view'] },
    ],
  },
  {
    id: 'billing',
    labelKey: 'workspace.billing.label',
    label: 'Billing',
    titleKey: 'workspace.billing.title',
    title: 'Billing',
    subtitleKey: 'workspace.billing.subtitle',
    subtitle: 'Utility pricing, billing cycles, and imported statements.',
    route: '/billing',
    workspacePermissions: ['utility_accounts.view', 'rates.manage_custom', 'rates.manage_sources', 'rates.assign', 'utility_bills.view'],
    tabs: [
      { id: 'accounts', labelKey: 'workspace.billing.accounts', label: 'Utility Accounts', route: '/billing/accounts', permissions: ['utility_accounts.view'] },
      { id: 'rate-plans', labelKey: 'workspace.billing.rate_plans', label: 'Rate Plans', route: '/billing/rate-plans', permissions: ['rates.view'] },
      { id: 'rate-sources', labelKey: 'workspace.billing.rate_sources', label: 'Rate Sources', route: '/billing/rate-sources', permissions: ['rates.manage_sources'] },
    ],
  },
  {
    id: 'alerts',
    labelKey: 'workspace.alerts.label',
    label: 'Alerts',
    titleKey: 'workspace.alerts.title',
    title: 'Alerts',
    subtitleKey: 'workspace.alerts.subtitle',
    subtitle: 'Review operational alerts and notification delivery.',
    route: '/alerts',
    tabs: [{ id: 'alerts', labelKey: 'workspace.alerts.label', label: 'Alerts', route: '/alerts', permissions: ['alerts.view'] }],
  },
  {
    id: 'administration',
    labelKey: 'workspace.administration.label',
    label: 'Administration',
    titleKey: 'workspace.administration.title',
    title: 'Administration',
    subtitleKey: 'workspace.administration.subtitle',
    subtitle: 'Manage access, sites, delivery, data, interface, and security.',
    route: '/administration',
    workspacePermissions: [
      'users.view', 'sites.create', 'sites.edit', 'network.view', 'alerts.manage_delivery',
      'backups.view', 'logs.export', 'interface_text.view', 'status_indicators.view',
      'settings.view', 'audit.view',
    ],
    tabs: [
      { id: 'access', labelKey: 'workspace.administration.access', label: 'Access', route: '/administration/access', permissions: ['users.view'] },
      { id: 'sites-network', labelKey: 'workspace.administration.sites_network', label: 'Sites & Network', route: '/administration/sites-network', permissions: ['sites.view', 'sites.create', 'sites.edit', 'network.view'] },
      { id: 'notifications', labelKey: 'workspace.administration.notifications', label: 'Notification Settings', route: '/administration/notifications', permissions: ['alerts.manage_delivery'] },
      { id: 'data', labelKey: 'workspace.administration.data', label: 'Data Management', route: '/administration/data', permissions: ['backups.view', 'logs.export'] },
      { id: 'interface', labelKey: 'workspace.administration.interface', label: 'Interface', route: '/administration/interface', permissions: ['interface_text.view', 'status_indicators.view'] },
      { id: 'security', labelKey: 'workspace.administration.security', label: 'Security', route: '/administration/security', permissions: ['settings.view', 'audit.view'] },
    ],
  },
]

export function permittedTabs(workspace: WorkspaceDefinition, session: Session): WorkspaceTab[] {
  const permissions = sessionPermissions(session)
  return workspace.tabs.filter((tab) => tab.permissions.some((permission) => permissions.has(permission)))
}

export function canOpenWorkspace(workspace: WorkspaceDefinition, session: Session): boolean {
  const permissions = sessionPermissions(session)
  if (workspace.workspacePermissions && !workspace.workspacePermissions.some((permission) => permissions.has(permission))) return false
  return permittedTabs(workspace, session).length > 0
}

export function workspaceById(id: WorkspaceId): WorkspaceDefinition {
  const workspace = WORKSPACES.find((item) => item.id === id)
  if (!workspace) throw new Error(`Unknown workspace: ${id}`)
  return workspace
}

export function WorkspaceShell({
  workspaceId,
  session,
  children,
}: {
  workspaceId: WorkspaceId
  session: Session
  children: ReactNode
}) {
  const workspace = workspaceById(workspaceId)
  const tabs = permittedTabs(workspace, session)
  const location = useLocation()
  const { text } = useInterfaceText()
  const title = text(workspace.titleKey, workspace.title)
  const subtitle = text(workspace.subtitleKey, workspace.subtitle)
  useEffect(() => {
    document.title = `${title} · ${text('general.browser_title_prefix', 'Power Monitor')}`
  }, [text, title])
  const showHeader = workspaceId !== 'overview'
  return (
    <ActionScope scopeKey={`${location.pathname}${location.search}`}>
      <section className={`workspace workspace-${workspaceId}`} aria-labelledby={showHeader ? `${workspaceId}-workspace-title` : undefined}>
        {showHeader && (
          <header className="workspace-header">
            <div>
              <span className="eyebrow">Workspace</span>
              <h1 id={`${workspaceId}-workspace-title`}>{title}</h1>
              <p>{subtitle}</p>
            </div>
            <StatusIndicatorZone zone="workspace_header" className="workspace-header-status" />
          </header>
        )}
        {tabs.length > 1 && (
          <nav className="workspace-tab-bar" aria-label={`${title} sections`} role="tablist">
            {tabs.map((tab) => {
              const selected = location.pathname === tab.route || location.pathname.startsWith(`${tab.route}/`)
              return (
                <NavLink
                  key={tab.id}
                  to={tab.route}
                  role="tab"
                  aria-selected={selected}
                  className={selected ? 'active' : undefined}
                >
                  {text(tab.labelKey, tab.label)}
                </NavLink>
              )
            })}
          </nav>
        )}
        <StatusIndicatorZone zone="page_summary" />
        <div className="workspace-content">
          <WorkspacePageContext.Provider value={showHeader}>{children}</WorkspacePageContext.Provider>
        </div>
      </section>
    </ActionScope>
  )
}
