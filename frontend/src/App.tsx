import { useQuery } from '@tanstack/react-query'
import { Component, lazy, Suspense, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { hasPermission } from './access'
import { api } from './api'
import { Layout } from './components/Layout'
import { StatusIndicatorProvider } from './components/StatusIndicators'
import { ErrorState, LoadingState } from './components/UI'
import { InterfaceTextProvider } from './interfaceText'
import { AccessDeniedPage } from './pages/AccessDeniedPage'
import { AuthPage } from './pages/AuthPage'
import type { Session } from './types'
import {
  canOpenWorkspace,
  permittedTabs,
  WorkspaceShell,
  workspaceById,
  type WorkspaceId,
} from './workspaces'

const AlertsPage = lazy(() => import('./pages/AlertsPage').then((module) => ({ default: module.AlertsPage })))
const DashboardPage = lazy(() => import('./pages/DashboardPage').then((module) => ({ default: module.DashboardPage })))
const DeviceDetailPage = lazy(() => import('./pages/DeviceDetailPage').then((module) => ({ default: module.DeviceDetailPage })))
const DevicesPage = lazy(() => import('./pages/DevicesPage').then((module) => ({ default: module.DevicesPage })))
const EnrollmentPage = lazy(() => import('./pages/EnrollmentPage').then((module) => ({ default: module.EnrollmentPage })))
const HistoryPage = lazy(() => import('./pages/HistoryPage').then((module) => ({ default: module.HistoryPage })))
const UsagePage = lazy(() => import('./pages/UsagePage').then((module) => ({ default: module.UsagePage })))
const CostsPage = lazy(() => import('./pages/CostsPage').then((module) => ({ default: module.CostsPage })))
const RatesPage = lazy(() => import('./pages/RatesPage').then((module) => ({ default: module.RatesPage })))
const RateEditorPage = lazy(() => import('./pages/RateEditorPage').then((module) => ({ default: module.RateEditorPage })))
const RateSourcesPage = lazy(() => import('./pages/RateSourcesPage').then((module) => ({ default: module.RateSourcesPage })))
const TopologyPage = lazy(() => import('./pages/TopologyPage').then((module) => ({ default: module.TopologyPage })))
const UsersAccessPage = lazy(() => import('./pages/UsersAccessPage').then((module) => ({ default: module.UsersAccessPage })))
const BillingAccountsPage = lazy(() => import('./pages/WorkspacePages').then((module) => ({ default: module.BillingAccountsPage })))
const SitesNetworkPage = lazy(() => import('./pages/WorkspacePages').then((module) => ({ default: module.SitesNetworkPage })))
const NotificationsWorkspacePage = lazy(() => import('./pages/WorkspacePages').then((module) => ({ default: module.NotificationsWorkspacePage })))
const DataManagementPage = lazy(() => import('./pages/WorkspacePages').then((module) => ({ default: module.DataManagementPage })))
const InterfaceWorkspacePage = lazy(() => import('./pages/WorkspacePages').then((module) => ({ default: module.InterfaceWorkspacePage })))
const SecurityWorkspacePage = lazy(() => import('./pages/WorkspacePages').then((module) => ({ default: module.SecurityWorkspacePage })))

function Guard({ session, permission, children }: { session: Session; permission: string; children: ReactNode }) {
  return hasPermission(session, permission) ? children : <AccessDeniedPage permission={permission} />
}

function AnyGuard({
  session,
  permissions,
  children,
}: {
  session: Session
  permissions: string[]
  children: ReactNode
}) {
  return permissions.some((permission) => hasPermission(session, permission))
    ? children
    : <AccessDeniedPage permission={permissions.join(' or ')} />
}

class WorkspaceErrorBoundary extends Component<{ children: ReactNode; resetKey: string }, { error?: Error }> {
  state: { error?: Error } = {}

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidUpdate(previous: Readonly<{ children: ReactNode; resetKey: string }>) {
    if (this.state.error && previous.resetKey !== this.props.resetKey) {
      this.setState({ error: undefined })
    }
  }

  render() {
    if (this.state.error) {
      return <ErrorState error={this.state.error} retry={() => { window.location.reload() }} />
    }
    return this.props.children
  }
}

function CanonicalRedirect({ to, transform }: { to: string; transform?: (params: URLSearchParams) => void }) {
  const location = useLocation()
  const params = new URLSearchParams(location.search)
  transform?.(params)
  return <Navigate to={{ pathname: to, search: params.toString(), hash: location.hash }} replace />
}

function LegacyAdminRedirect() {
  const location = useLocation()
  const params = new URLSearchParams(location.search)
  const tab = params.get('tab')
  params.delete('tab')
  let path = '/administration/sites-network'
  if (['users', 'users-roles', 'roles'].includes(tab ?? '')) path = '/administration/access'
  else if (tab === 'sites-accounts') path = '/billing/accounts'
  else if (tab === 'notifications') path = '/administration/notifications'
  else if (tab === 'backups') path = '/administration/data'
  else if (tab === 'server-network') {
    path = '/administration/sites-network'
    params.set('view', 'network')
  } else if (tab === 'security-audit') {
    path = '/administration/security'
    params.set('view', 'audit')
  }
  const safeParameters: Record<string, Set<string>> = {
    '/administration/access': new Set(['search', 'status', 'role', 'site', 'mfa', 'protected', 'role_kind']),
    '/billing/accounts': new Set(['rate_version_id', 'site', 'create']),
    '/administration/notifications': new Set(),
    '/administration/data': new Set(),
    '/administration/sites-network': new Set(['view', 'site', 'search', 'status']),
    '/administration/security': new Set(['view']),
  }
  const allowed = safeParameters[path] ?? new Set<string>()
  for (const key of [...params.keys()]) {
    if (!allowed.has(key)) params.delete(key)
  }
  return <Navigate to={{ pathname: path, search: params.toString(), hash: location.hash }} replace />
}

function WorkspaceIndexRedirect({ session, workspaceId }: { session: Session; workspaceId: WorkspaceId }) {
  const workspace = workspaceById(workspaceId)
  const first = permittedTabs(workspace, session)[0]
  return first ? <CanonicalRedirect to={first.route} /> : <AccessDeniedPage permission={`${workspaceId}.view`} />
}

function InWorkspace({
  session,
  workspaceId,
  children,
}: {
  session: Session
  workspaceId: WorkspaceId
  children: ReactNode
}) {
  const workspace = workspaceById(workspaceId)
  if (!canOpenWorkspace(workspace, session)) return <AccessDeniedPage permission={`${workspaceId}.view`} />
  return <WorkspaceShell workspaceId={workspaceId} session={session}>{children}</WorkspaceShell>
}

function ProtectedApp({ session }: { session: Session }) {
  const location = useLocation()
  if (!session.authenticated) return <Navigate to="/sign-in" replace state={{ from: location.pathname }} />
  const canManageRates = hasPermission(session, 'rates.manage_custom')
  const canManageBills = hasPermission(session, 'utility_bills.manage')
  return (
    <InterfaceTextProvider>
      <StatusIndicatorProvider>
        <Layout session={session}>
          <WorkspaceErrorBoundary resetKey={`${location.pathname}${location.search}`}>
            <Suspense fallback={<LoadingState label="Opening this workspace…" />}>
              <Routes>
                <Route path="/" element={<Navigate to="/overview" replace />} />

                <Route path="/overview" element={<InWorkspace session={session} workspaceId="overview"><Guard session={session} permission="overview.view"><DashboardPage canEnroll={hasPermission(session, 'enrollment.manage')} /></Guard></InWorkspace>} />

                <Route path="/monitoring" element={<WorkspaceIndexRedirect session={session} workspaceId="monitoring" />} />
                <Route path="/monitoring/devices" element={<InWorkspace session={session} workspaceId="monitoring"><Guard session={session} permission="devices.view"><DevicesPage /></Guard></InWorkspace>} />
                <Route path="/monitoring/devices/:deviceId" element={<InWorkspace session={session} workspaceId="monitoring"><Guard session={session} permission="devices.view"><DeviceDetailPage /></Guard></InWorkspace>} />
                <Route path="/monitoring/topology" element={<InWorkspace session={session} workspaceId="monitoring"><Guard session={session} permission="topology.view"><TopologyPage /></Guard></InWorkspace>} />
                <Route path="/monitoring/enrollment" element={<InWorkspace session={session} workspaceId="monitoring"><Guard session={session} permission="enrollment.view"><EnrollmentPage /></Guard></InWorkspace>} />

                <Route path="/analytics" element={<WorkspaceIndexRedirect session={session} workspaceId="analytics" />} />
                <Route path="/analytics/usage" element={<InWorkspace session={session} workspaceId="analytics"><Guard session={session} permission="usage.view"><UsagePage /></Guard></InWorkspace>} />
                <Route path="/analytics/history" element={<InWorkspace session={session} workspaceId="analytics"><Guard session={session} permission="history.view"><HistoryPage /></Guard></InWorkspace>} />
                <Route path="/analytics/costs" element={<InWorkspace session={session} workspaceId="analytics"><Guard session={session} permission="costs.view"><CostsPage canManageBills={canManageBills} /></Guard></InWorkspace>} />

                <Route path="/billing" element={<WorkspaceIndexRedirect session={session} workspaceId="billing" />} />
                <Route path="/billing/accounts" element={<InWorkspace session={session} workspaceId="billing"><Guard session={session} permission="utility_accounts.view"><BillingAccountsPage canViewBills={hasPermission(session, 'utility_bills.view')} /></Guard></InWorkspace>} />
                <Route path="/billing/rate-plans" element={<InWorkspace session={session} workspaceId="billing"><Guard session={session} permission="rates.view"><RatesPage canManage={canManageRates} canImportBills={canManageBills} /></Guard></InWorkspace>} />
                <Route path="/billing/rate-plans/new" element={<InWorkspace session={session} workspaceId="billing"><Guard session={session} permission="rates.manage_custom"><RateEditorPage canManage canImportBills={canManageBills} /></Guard></InWorkspace>} />
                <Route path="/billing/rate-plans/:planId/versions/:versionId" element={<InWorkspace session={session} workspaceId="billing"><Guard session={session} permission="rates.view"><RateEditorPage canManage={canManageRates} canImportBills={canManageBills} /></Guard></InWorkspace>} />
                <Route path="/billing/rate-sources" element={<InWorkspace session={session} workspaceId="billing"><Guard session={session} permission="rates.manage_sources"><RateSourcesPage /></Guard></InWorkspace>} />

                <Route path="/alerts" element={<InWorkspace session={session} workspaceId="alerts"><Guard session={session} permission="alerts.view"><AlertsPage /></Guard></InWorkspace>} />

                <Route path="/administration" element={<WorkspaceIndexRedirect session={session} workspaceId="administration" />} />
                <Route path="/administration/access" element={<InWorkspace session={session} workspaceId="administration"><Guard session={session} permission="users.view"><UsersAccessPage session={session} /></Guard></InWorkspace>} />
                <Route path="/administration/sites-network" element={<InWorkspace session={session} workspaceId="administration"><AnyGuard session={session} permissions={['sites.view', 'network.view']}><SitesNetworkPage session={session} /></AnyGuard></InWorkspace>} />
                <Route path="/administration/notifications" element={<InWorkspace session={session} workspaceId="administration"><Guard session={session} permission="alerts.manage_delivery"><NotificationsWorkspacePage /></Guard></InWorkspace>} />
                <Route path="/administration/data" element={<InWorkspace session={session} workspaceId="administration"><AnyGuard session={session} permissions={['backups.view', 'logs.export']}><DataManagementPage session={session} /></AnyGuard></InWorkspace>} />
                <Route path="/administration/interface" element={<InWorkspace session={session} workspaceId="administration"><AnyGuard session={session} permissions={['interface_text.view', 'status_indicators.view']}><InterfaceWorkspacePage session={session} /></AnyGuard></InWorkspace>} />
                <Route path="/administration/security" element={<InWorkspace session={session} workspaceId="administration"><AnyGuard session={session} permissions={['settings.view', 'audit.view']}><SecurityWorkspacePage session={session} /></AnyGuard></InWorkspace>} />

                <Route path="/devices" element={<CanonicalRedirect to="/monitoring/devices" />} />
                <Route path="/devices/:deviceId" element={<LegacyDeviceRedirect />} />
                <Route path="/topology" element={<CanonicalRedirect to="/monitoring/topology" />} />
                <Route path="/enrollment" element={<CanonicalRedirect to="/monitoring/enrollment" />} />
                <Route path="/usage" element={<CanonicalRedirect to="/analytics/usage" />} />
                <Route path="/history" element={<CanonicalRedirect to="/analytics/history" />} />
                <Route path="/costs" element={<CanonicalRedirect to="/analytics/costs" />} />
                <Route path="/rates" element={<CanonicalRedirect to="/billing/rate-plans" />} />
                <Route path="/rates/new" element={<CanonicalRedirect to="/billing/rate-plans/new" />} />
                <Route path="/rates/import-bill" element={<CanonicalRedirect to="/billing/rate-plans/new" transform={(params) => { params.set('bill_import', 'open') }} />} />
                <Route path="/rates/:planId/versions/:versionId" element={<LegacyRateVersionRedirect />} />
                <Route path="/rates/sources" element={<CanonicalRedirect to="/billing/rate-sources" />} />
                <Route path="/reports" element={<CanonicalRedirect to="/analytics/history" transform={(params) => { params.set('export', 'open') }} />} />
                <Route path="/admin" element={<LegacyAdminRedirect />} />
                <Route path="/administration/users" element={<CanonicalRedirect to="/administration/access" />} />
                <Route path="/administration/users-roles" element={<CanonicalRedirect to="/administration/access" />} />
                <Route path="/administration/roles" element={<CanonicalRedirect to="/administration/access" />} />
                <Route path="/administration/users-access" element={<CanonicalRedirect to="/administration/access" />} />
                <Route path="/administration/interface-text" element={<CanonicalRedirect to="/administration/interface" transform={(params) => { params.set('view', 'text') }} />} />
                <Route path="/administration/status-indicators" element={<CanonicalRedirect to="/administration/interface" transform={(params) => { params.set('view', 'layout') }} />} />
                <Route path="/administration/system-health" element={<CanonicalRedirect to="/administration/security" transform={(params) => { params.set('view', 'health') }} />} />
                <Route path="*" element={<Navigate to="/overview" replace />} />
              </Routes>
            </Suspense>
          </WorkspaceErrorBoundary>
        </Layout>
      </StatusIndicatorProvider>
    </InterfaceTextProvider>
  )
}

function LegacyDeviceRedirect() {
  const location = useLocation()
  const deviceId = location.pathname.split('/')[2]
  return <CanonicalRedirect to={`/monitoring/devices/${deviceId}`} />
}

function LegacyRateVersionRedirect() {
  const location = useLocation()
  const values = location.pathname.split('/')
  return <CanonicalRedirect to={`/billing/rate-plans/${values[2]}/versions/${values[4]}`} />
}

export default function App() {
  const session = useQuery({ queryKey: ['session'], queryFn: () => api<Session>('/api/v1/auth/session'), retry: false })
  if (session.isLoading) return <main className="startup"><LoadingState label="Establishing a secure session…" /></main>
  const value = session.data ?? { authenticated: false, bootstrap_required: false }
  return (
    <Routes>
      <Route path="/sign-in" element={value.authenticated ? <Navigate to="/overview" replace /> : <AuthPage bootstrapRequired={value.bootstrap_required} />} />
      <Route path="/*" element={<ProtectedApp session={value} />} />
    </Routes>
  )
}
