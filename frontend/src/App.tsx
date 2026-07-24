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

const AdminPage = lazy(() => import('./pages/AdminPage').then((module) => ({ default: module.AdminPage })))
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
const ReportsPage = lazy(() => import('./pages/ReportsPage').then((module) => ({ default: module.ReportsPage })))
const TopologyPage = lazy(() => import('./pages/TopologyPage').then((module) => ({ default: module.TopologyPage })))
const UsersAccessPage = lazy(() => import('./pages/UsersAccessPage').then((module) => ({ default: module.UsersAccessPage })))
const InterfaceTextPage = lazy(() => import('./pages/InterfaceTextPage').then((module) => ({ default: module.InterfaceTextPage })))
const StatusIndicatorsPage = lazy(() => import('./pages/StatusIndicatorsPage').then((module) => ({ default: module.StatusIndicatorsPage })))
const SystemHealthPage = lazy(() => import('./pages/SystemHealthPage').then((module) => ({ default: module.SystemHealthPage })))

function Guard({ session, permission, children }: { session: Session; permission: string; children: ReactNode }) {
  return hasPermission(session, permission) ? children : <AccessDeniedPage permission={permission} />
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

function LegacyBillImportRedirect() {
  const location = useLocation()
  const params = new URLSearchParams(location.search)
  params.set('bill_import', 'open')
  return <Navigate to={`/rates/new?${params.toString()}`} replace />
}

function LegacyUserManagementRedirect() {
  const location = useLocation()
  const current = new URLSearchParams(location.search)
  const preserved = new URLSearchParams()
  for (const key of ['search', 'status', 'role', 'site', 'mfa']) {
    for (const value of current.getAll(key)) preserved.append(key, value)
  }
  const query = preserved.toString()
  return <Navigate to={`/administration/users-access${query ? `?${query}` : ''}`} replace />
}

function ProtectedApp({ session }: { session: Session }) {
  const location = useLocation()
  if (!session.authenticated) return <Navigate to="/sign-in" replace state={{ from: location.pathname }} />
  const canManageRates = hasPermission(session, 'rates.manage_custom')
  const canManageBills = hasPermission(session, 'utility_bills.manage')
  const legacyAdminTab = new URLSearchParams(location.search).get('tab')
  const legacyUserTab = ['users', 'users-roles', 'roles'].includes(legacyAdminTab ?? '')
  return (
    <InterfaceTextProvider>
      <StatusIndicatorProvider>
        <Layout session={session}>
        <WorkspaceErrorBoundary resetKey={`${location.pathname}${location.search}`}>
        <Suspense fallback={<LoadingState label="Opening this workspace…" />}>
        <Routes>
        <Route path="/" element={<Guard session={session} permission="overview.view"><DashboardPage canEnroll={hasPermission(session, 'enrollment.manage')} /></Guard>} />
        <Route path="/devices" element={<Guard session={session} permission="devices.view"><DevicesPage /></Guard>} />
        <Route path="/devices/:deviceId" element={<Guard session={session} permission="devices.view"><DeviceDetailPage /></Guard>} />
        <Route path="/topology" element={<Guard session={session} permission="topology.view"><TopologyPage /></Guard>} />
        <Route path="/history" element={<Guard session={session} permission="history.view"><HistoryPage /></Guard>} />
        <Route path="/usage" element={<Guard session={session} permission="usage.view"><UsagePage /></Guard>} />
        <Route path="/costs" element={<Guard session={session} permission="costs.view"><CostsPage canManageBills={canManageBills} /></Guard>} />
        <Route path="/rates" element={<Guard session={session} permission="rates.view"><RatesPage canManage={canManageRates} canImportBills={canManageBills} /></Guard>} />
        <Route path="/rates/new" element={<Guard session={session} permission="rates.manage_custom"><RateEditorPage canManage canImportBills={canManageBills} /></Guard>} />
        <Route path="/rates/import-bill" element={<Guard session={session} permission="utility_bills.manage"><LegacyBillImportRedirect /></Guard>} />
        <Route path="/rates/:planId/versions/:versionId" element={<Guard session={session} permission="rates.view"><RateEditorPage canManage={canManageRates} canImportBills={canManageBills} /></Guard>} />
        <Route path="/rates/sources" element={<Guard session={session} permission="rates.manage_sources"><RateSourcesPage /></Guard>} />
        <Route path="/alerts" element={<Guard session={session} permission="alerts.view"><AlertsPage /></Guard>} />
        <Route path="/enrollment" element={<Guard session={session} permission="enrollment.view"><EnrollmentPage /></Guard>} />
        <Route path="/reports" element={<Guard session={session} permission="history.export"><ReportsPage /></Guard>} />
        <Route path="/admin" element={legacyUserTab ? <LegacyUserManagementRedirect /> : <Guard session={session} permission="settings.view"><AdminPage /></Guard>} />
        <Route path="/administration/users" element={<LegacyUserManagementRedirect />} />
        <Route path="/administration/users-roles" element={<LegacyUserManagementRedirect />} />
        <Route path="/administration/roles" element={<LegacyUserManagementRedirect />} />
        <Route path="/administration/users-access" element={<Guard session={session} permission="users.view"><UsersAccessPage session={session} /></Guard>} />
        <Route path="/administration/interface-text" element={<Guard session={session} permission="interface_text.view"><InterfaceTextPage canManage={hasPermission(session, 'interface_text.manage')} /></Guard>} />
        <Route path="/administration/status-indicators" element={<Guard session={session} permission="status_indicators.view"><StatusIndicatorsPage canManage={hasPermission(session, 'status_indicators.manage')} /></Guard>} />
        <Route path="/administration/system-health" element={<Guard session={session} permission="settings.view"><SystemHealthPage /></Guard>} />
        <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </Suspense>
        </WorkspaceErrorBoundary>
        </Layout>
      </StatusIndicatorProvider>
    </InterfaceTextProvider>
  )
}

export default function App() {
  const session = useQuery({ queryKey: ['session'], queryFn: () => api<Session>('/api/v1/auth/session'), retry: false })
  if (session.isLoading) return <main className="startup"><LoadingState label="Establishing a secure session…" /></main>
  const value = session.data ?? { authenticated: false, bootstrap_required: false }
  return (
    <Routes>
      <Route path="/sign-in" element={value.authenticated ? <Navigate to="/" replace /> : <AuthPage bootstrapRequired={value.bootstrap_required} />} />
      <Route path="/*" element={<ProtectedApp session={value} />} />
    </Routes>
  )
}
