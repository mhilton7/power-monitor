import { useQuery } from '@tanstack/react-query'
import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { api } from './api'
import { Layout } from './components/Layout'
import { LoadingState } from './components/UI'
import { AuthPage } from './pages/AuthPage'
import type { Session } from './types'

const AdminPage = lazy(() => import('./pages/AdminPage').then((module) => ({ default: module.AdminPage })))
const AlertsPage = lazy(() => import('./pages/AlertsPage').then((module) => ({ default: module.AlertsPage })))
const CostsPage = lazy(() => import('./pages/CostsPage').then((module) => ({ default: module.CostsPage })))
const DashboardPage = lazy(() => import('./pages/DashboardPage').then((module) => ({ default: module.DashboardPage })))
const DeviceDetailPage = lazy(() => import('./pages/DeviceDetailPage').then((module) => ({ default: module.DeviceDetailPage })))
const DevicesPage = lazy(() => import('./pages/DevicesPage').then((module) => ({ default: module.DevicesPage })))
const EnrollmentPage = lazy(() => import('./pages/EnrollmentPage').then((module) => ({ default: module.EnrollmentPage })))
const HistoryPage = lazy(() => import('./pages/HistoryPage').then((module) => ({ default: module.HistoryPage })))
const RatesPage = lazy(() => import('./pages/RatesPage').then((module) => ({ default: module.RatesPage })))
const ReportsPage = lazy(() => import('./pages/ReportsPage').then((module) => ({ default: module.ReportsPage })))
const TopologyPage = lazy(() => import('./pages/TopologyPage').then((module) => ({ default: module.TopologyPage })))

function ProtectedApp({ session }: { session: Session }) {
  const location = useLocation()
  if (!session.authenticated) return <Navigate to="/sign-in" replace state={{ from: location.pathname }} />
  const isAdmin = session.user?.roles.includes('admin') ?? false
  return (
    <Layout session={session}>
      <Suspense fallback={<LoadingState label="Opening this workspace…" />}>
        <Routes>
        <Route path="/" element={<DashboardPage canEnroll={isAdmin} />} />
        <Route path="/devices" element={<DevicesPage />} />
        <Route path="/devices/:deviceId" element={<DeviceDetailPage />} />
        <Route path="/topology" element={<TopologyPage />} />
        <Route path="/history" element={<HistoryPage />} />
        <Route path="/costs" element={<CostsPage />} />
        <Route path="/rates" element={<RatesPage />} />
        <Route path="/alerts" element={<AlertsPage />} />
        <Route path="/enrollment" element={isAdmin ? <EnrollmentPage /> : <Navigate to="/" replace />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/admin" element={isAdmin ? <AdminPage currentUserId={session.user?.id} /> : <Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </Layout>
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
