import { useQuery } from '@tanstack/react-query'
import { createContext, useContext, useMemo, type ReactNode } from 'react'
import { api } from './api'

export interface InterfaceTextPayload {
  revision: number
  values: Record<string, string>
}

export const INTERFACE_TEXT_DEFAULTS: Record<string, string> = {
  'general.application_name': 'Power Monitor',
  'general.application_short_name': 'Power Monitor',
  'general.dashboard_welcome_heading': 'Power Dashboard',
  'general.dashboard_welcome_subtitle': 'Monitor energy use, costs, device status, and site performance in one place.',
  'general.organization_tagline': 'Local energy intelligence',
  'general.browser_title_prefix': 'Power Monitor',
  'login.heading': 'Sign in to your dashboard',
  'login.subtitle': 'Use your local Power Monitor account to continue.',
  'login.email_label': 'Email address',
  'login.password_label': 'Password',
  'login.sign_in_button': 'Sign in',
  'login.help_text': 'Use your local account credentials. Contact your administrator if you need access.',
  'login.support_label': 'Contact support',
  'login.support_url': '',
  'login.footer': 'Local account · Secure session · Audited access',
  'navigation.overview': 'Overview',
  'navigation.devices': 'Devices',
  'navigation.topology': 'Topology',
  'navigation.usage': 'Usage',
  'navigation.history': 'History',
  'navigation.costs': 'Costs',
  'navigation.rates': 'Rates',
  'navigation.alerts': 'Alerts & Notifications',
  'navigation.enrollment': 'Enrollment',
  'navigation.backups': 'Backups',
  'navigation.administration': 'Administration',
  'navigation.users_access': 'Users & Access',
  'navigation.interface_text': 'Dashboard & Login Text',
  'navigation.status_indicators': 'Status Indicators & Layout',
  'navigation.system_health': 'System Health',
  'pages.overview.title': 'Power Dashboard',
  'pages.overview.subtitle': 'Monitor energy use, costs, device status, and site performance in one place.',
  'pages.devices.title': 'Device Management',
  'pages.devices.subtitle': 'Sensor health and general data',
  'pages.topology.title': 'Site & circuit topology',
  'pages.topology.subtitle': 'Make overlap explicit so parent, service-leg, branch, and submeter readings never become an accidental total.',
  'pages.usage.title': 'Usage by Time of Day',
  'pages.usage.subtitle': 'Understand when monitored energy is used.',
  'pages.history.title': 'History & comparison',
  'pages.history.subtitle': 'Raw UTC intervals are rendered in your locale, with gaps and quality limitations kept visible.',
  'pages.costs.title': 'Costs',
  'pages.costs.subtitle': 'Estimated energy costs for permitted sites.',
  'pages.rates.title': 'Rate plans',
  'pages.rates.subtitle': 'Effective-dated, source-backed versions preserve historical estimates while new utility changes remain reviewable.',
  'pages.rate_sources.title': 'SCE rate sources',
  'pages.rate_sources.subtitle': 'Approved sources are fetched, hashed, archived, parsed, and compared. No candidate changes an active rate without the configured approval workflow.',
  'pages.alerts.title': 'Alerts & Notifications',
  'pages.alerts.subtitle': 'Review operational alerts and notification delivery.',
  'pages.enrollment.title': 'Multi-device enrollment',
  'pages.enrollment.subtitle': 'Prepare a separate short-lived, single-use token for every ESP32 sensor.',
  'pages.backups.title': 'Backups',
  'pages.backups.subtitle': 'Verified logical backups, restores, and redacted log exports.',
  'pages.users_access.title': 'Users & Access',
  'pages.users_access.subtitle': 'Manage user roles, permissions, site access, account status, and active sessions.',
  'pages.interface_text.title': 'Dashboard & Login Text',
  'pages.interface_text.subtitle': 'Customize approved interface labels and messages without changing application routes or security behavior.',
  'pages.status_indicators.title': 'Status Indicators & Layout',
  'pages.status_indicators.subtitle': 'Choose which status indicators are visible, where they appear, and how the dashboard reorganizes them across screen sizes.',
  'pages.system_health.title': 'System Health',
  'pages.system_health.subtitle': 'Review API, database, and background-worker readiness without crowding normal monitoring pages.',
  'pages.administration.title': 'Administration',
  'pages.administration.subtitle': 'Manage local users, site boundaries, verified backups, security evidence, and server health.',
  'footer.dashboard': 'Power Monitor Server',
  'footer.support_label': 'Support',
  'footer.support_url': '',
  'footer.copyright': '',
  'footer.banner': '',
}

type InterfaceTextContextValue = {
  revision: number
  values: Record<string, string>
  text: (key: string, fallback?: string) => string
}

const InterfaceTextContext = createContext<InterfaceTextContextValue>({
  revision: 0,
  values: INTERFACE_TEXT_DEFAULTS,
  text: (key, fallback) => INTERFACE_TEXT_DEFAULTS[key] ?? fallback ?? key,
})

const CLIENT_REQUIRED_KEYS = new Set([
  'general.application_name',
  'general.application_short_name',
  'login.heading',
  'login.subtitle',
  'login.email_label',
  'login.password_label',
  'login.sign_in_button',
])

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function safeRevision(payload?: unknown): number {
  if (!isRecord(payload)) return 0
  const revision = payload.revision
  return typeof revision === 'number' && Number.isInteger(revision) && revision >= 0 ? revision : 0
}

function hasUnsafeControlCharacter(value: string): boolean {
  for (const character of value) {
    const code = character.charCodeAt(0)
    if (code <= 8 || code === 11 || code === 12 || (code >= 14 && code <= 31) || code === 127) return true
  }
  return false
}

function safeClientValue(key: string, value: string): boolean {
  if (value.length > 500 || hasUnsafeControlCharacter(value)) return false
  if (/<\s*\/?\s*[a-zA-Z!][^>]*>/u.test(value) || value.includes('{{') || value.includes('{%')) return false
  if (CLIENT_REQUIRED_KEYS.has(key) && !value.trim()) return false
  if (key.endsWith('_url') && value) {
    try {
      const url = new URL(value)
      return (url.protocol === 'https:' || url.protocol === 'mailto:') && !url.username && !url.password
    } catch {
      return false
    }
  }
  return true
}

function safeValues(payload?: unknown): Record<string, string> {
  const values = { ...INTERFACE_TEXT_DEFAULTS }
  if (!isRecord(payload) || safeRevision(payload) !== payload.revision || !isRecord(payload.values)) return values
  for (const [key, value] of Object.entries(payload.values)) {
    if (key in values && typeof value === 'string' && safeClientValue(key, value)) values[key] = value
  }
  return values
}

export function InterfaceTextProvider({ children }: { children: ReactNode }) {
  const query = useQuery({
    queryKey: ['interface-text'],
    queryFn: () => api<InterfaceTextPayload>('/api/v1/interface-text'),
    retry: false,
    staleTime: 60_000,
  })
  const value = useMemo<InterfaceTextContextValue>(() => {
    const values = safeValues(query.data)
    return {
      revision: safeRevision(query.data),
      values,
      text: (key, fallback) => values[key] ?? fallback ?? key,
    }
  }, [query.data])
  return <InterfaceTextContext.Provider value={value}>{children}</InterfaceTextContext.Provider>
}

export function useInterfaceText(): InterfaceTextContextValue {
  return useContext(InterfaceTextContext)
}

export function usePublicInterfaceText(): InterfaceTextContextValue {
  const query = useQuery({
    queryKey: ['public-interface-text'],
    queryFn: () => api<InterfaceTextPayload>('/api/v1/public/interface-text'),
    retry: false,
    staleTime: 60_000,
  })
  return useMemo(() => {
    const values = safeValues(query.data)
    return {
      revision: safeRevision(query.data),
      values,
      text: (key: string, fallback?: string) => values[key] ?? fallback ?? key,
    }
  }, [query.data])
}
