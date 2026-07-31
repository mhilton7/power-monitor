import { Link, Navigate, useLocation } from './router'
import { lazy, Suspense } from 'react'
import { AppErrorBoundary } from './ErrorBoundaries'
import { AppShell } from './AppShell'
import { LoadingState, ErrorState, EmptyState } from '../components/feedback/States'
import { SignInPage } from '../pages/auth/SignInPage'
import { OnboardingPage } from '../pages/onboarding/OnboardingPage'
import { LiveHomeProvider } from '../state/LiveHomeContext'
import { SingleHomeProvider, useSingleHome } from '../state/SingleHomeContext'
import { TestModeProvider } from '../state/TestModeContext'
import { useAuth } from '../state/AuthContext'
import {
  canAccessSettings,
  ROUTE_POLICIES,
  SETTINGS_SECTION_POLICIES,
  satisfiesPolicy,
} from '../access/permissions'

const HomePage = lazy(() => import('../pages/home/HomePage').then((module) => ({ default: module.HomePage })))
const HistoryPage = lazy(() => import('../pages/history/HistoryPage').then((module) => ({ default: module.HistoryPage })))
const BillingPage = lazy(() => import('../pages/billing/BillingPage').then((module) => ({ default: module.BillingPage })))
const SettingsPage = lazy(() => import('../pages/settings/SettingsPage').then((module) => ({ default: module.SettingsPage })))

const LEGACY_REDIRECTS: Record<string, string> = {
  '/overview': '/home',
  '/dashboard': '/home',
  '/devices': '/settings/sensors',
  '/enrollment': '/settings/sensors?action=add',
  '/topology': '/settings/sensors?view=relationships',
  '/monitoring': '/home',
  '/analytics': '/history',
  '/rates': '/billing',
  '/rate-sources': '/billing',
  '/bill-import': '/billing?action=upload',
  '/costs': '/billing',
  '/usage': '/history',
  '/alerts': '/home?alerts=1',
  '/administration': '/settings',
  '/users-access': '/settings/family',
  '/status-indicators': '/settings/advanced/layout',
  '/system-health': '/settings/advanced/system-health',
  '/health': '/settings/advanced/system-health',
  '/sensor-test-mode': '/settings/advanced/sensor-test-mode',
}

export function App() {
  if (!__SINGLE_HOME_MODE__) {
    return <div className="full-state"><EmptyState title="Single Home Mode is disabled" message="Set VITE_SINGLE_HOME_MODE=true for this production frontend." /></div>
  }
  return <SingleHomeApp />
}

function SingleHomeApp() {
  const { session, loading, error, refresh } = useAuth()
  if (loading) return <div className="full-state"><LoadingState label="Opening your home…" /></div>
  if (error) return <div className="full-state"><ErrorState error={error} retry={() => void refresh()} /></div>
  if (!session?.authenticated || !session.user) return <SignInPage />
  return (
    <AppErrorBoundary>
      <SingleHomeProvider>
        <LiveHomeProvider>
          <TestModeProvider>
            <AuthenticatedRoutes />
          </TestModeProvider>
        </LiveHomeProvider>
      </SingleHomeProvider>
    </AppErrorBoundary>
  )
}

function AuthenticatedRoutes() {
  const { resolution, loading, error, refresh } = useSingleHome()
  const { session } = useAuth()
  const location = useLocation()
  if (loading) return <div className="full-state"><LoadingState label="Finding your home…" /></div>
  if (error) return <div className="full-state"><ErrorState error={error} retry={() => void refresh()} /></div>
  if (resolution?.state === 'multiple') {
    return <div className="full-state"><EmptyState title="Single Home Mode needs one active home" message={`${resolution.homes.length} active homes were found. Disable the extra homes in the previous release or restore a pre-cutover image, then try again.`} /></div>
  }
  if (resolution?.state === 'missing' && location.pathname !== '/onboarding') {
    return <Navigate to="/onboarding" replace />
  }
  if (resolution?.state === 'missing' || location.pathname === '/onboarding') return <OnboardingPage />
  const redirect = Object.entries(LEGACY_REDIRECTS).find(([from]) => location.pathname === from || location.pathname.startsWith(`${from}/`))
  if (redirect) return <Navigate to={redirect[1]} replace />
  const settingsSection = location.pathname.split('/')[2] as keyof typeof SETTINGS_SECTION_POLICIES | undefined
  const sectionPolicy = settingsSection ? SETTINGS_SECTION_POLICIES[settingsSection] : undefined
  const settingsAllowed = canAccessSettings(session)
    && (!settingsSection || (sectionPolicy ? satisfiesPolicy(session, sectionPolicy) : false))
  let page
  if (location.pathname === '/home') page = satisfiesPolicy(session, ROUTE_POLICIES.home) ? <HomePage /> : <AccessDenied />
  else if (location.pathname === '/history') page = satisfiesPolicy(session, ROUTE_POLICIES.history) ? <HistoryPage /> : <AccessDenied />
  else if (location.pathname === '/billing') page = satisfiesPolicy(session, ROUTE_POLICIES.billing) ? <BillingPage /> : <AccessDenied />
  else if (location.pathname === '/settings' || location.pathname.startsWith('/settings/')) page = settingsAllowed ? <SettingsPage /> : <AccessDenied />
  else page = <Navigate to="/home" replace />
  return (
    <AppShell>
      <Suspense fallback={<LoadingState label="Opening workspace…" />}>
        {page}
      </Suspense>
    </AppShell>
  )
}

function AccessDenied() {
  return (
    <div className="full-state">
      <EmptyState
        title="Access denied"
        message="Your account does not have permission to open this workspace."
        action={<Link className="button primary" to="/home">Return Home</Link>}
      />
    </div>
  )
}
