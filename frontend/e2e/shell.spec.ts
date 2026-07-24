import { expect, test, type Page } from '@playwright/test'
import { emptyRateDocument } from '../src/rates'
import type { AdminSite, SiteDependencySummary } from '../src/types'

const session = (roles: string[] = ['admin']) => ({
  authenticated: true,
  bootstrap_required: false,
  user: { id: 'user-1', email: 'owner@example.test', display_name: 'Fleet Owner', roles },
})

const site = { id: 'site-1', name: 'Upland Site', timezone: 'America/Los_Angeles', allowed_cidrs: [], allowed_domains: [], allow_public_polling: false }
const fleet = { current_load_w: '960', energy_today_kwh: '12.5', estimated_cost_today: '4.25', billing_cycle_energy_kwh: '244', estimated_billing_cycle_cost: '83.11', online_devices: 1, synchronized_devices: 1, total_devices: 1, active_alerts: 1, current_tou_bucket: 'on-peak', recent_peak_w: '1800', has_live_data: true, has_energy_data: true, has_cost_data: true, reporting_devices: 1, latest_heartbeat_at: '2026-07-20T19:05:00Z', coverage_percent: '100', disclosure: 'Estimate, not utility bill.' }
const device = { id: 'device-1', name: 'Garage HVAC', site_id: 'site-1', site_name: 'Upland Site', circuit_id: 'branch-1', circuit_name: 'Garage branch', connection_mode: 'hybrid', measurement_role: 'submeter', cost_scope: 'energy_only', included_in_default: true, ct_rating_amps: '100', status: 'online_synchronized', lifecycle_status: 'active', current_watts: '960', last_seen_at: '2026-07-20T06:00:00Z', firmware_version: '1.0.0', rssi_dbm: -52, pzem_ok: true, sd_ok: true, time_trusted: true, backlog: 0 }
const historyDevice = { ...device, id: 'device-2', hardware_id: 'esp32-main-leg-2', name: 'Main Panel L2', circuit_id: 'leg-2', circuit_name: 'Main Panel L2', measurement_role: 'service-leg', current_watts: '1000' }
const officialVersion = { id: 'rate-version-official', version: 1, effective_from: '2026-06-01', effective_through: null, status: 'active', source_kind: 'official_sce', source_checked_at: '2026-07-19T10:15:00Z', source_label: 'SCE archived evidence', integrity_sha256: 'a'.repeat(64), is_active: true, immutable: true, created_at: '2026-06-01T00:00:00Z' }
const officialPlan = { id: 'rate-plan-official', code: 'TOU-D-4-9PM', name: 'TOU-D 4 PM to 9 PM', description: 'Official SCE residential time-of-use plan.', plan_kind: 'official_sce', ownership_scope: 'global', currency: 'USD', timezone: 'America/Los_Angeles', status: 'active', versions: [officialVersion] }
const accessPermissions = [
  ['overview.view', 'Dashboard', 'View overview', false], ['sites.view', 'Sites', 'View sites', false],
  ['devices.view', 'Devices', 'View devices', false], ['devices.manage', 'Devices', 'Manage devices', false],
  ['users.view', 'Administration', 'View users', true], ['users.manage', 'Administration', 'Manage users', true],
  ['users.disable', 'Administration', 'Disable and enable users', true], ['users.remove', 'Administration', 'Remove users', true],
  ['users.restore', 'Administration', 'Restore users', true],
  ['roles.view', 'Administration', 'View roles', true], ['roles.manage', 'Administration', 'Manage roles', true],
  ['interface_text.view', 'Administration', 'View interface text', true], ['interface_text.manage', 'Administration', 'Manage interface text', true],
  ['status_indicators.view', 'Administration', 'View status layouts', false], ['status_indicators.manage', 'Administration', 'Manage status layouts', true],
].map(([code, group, label, highRisk]) => ({ code, group, label, description: `${String(label)} permission`, high_risk: Boolean(highRisk) }))
const textDefinitions = [
  ['general.application_name', 'General', 'Power Monitor', 'Application display name', 'public', 160],
  ['general.application_short_name', 'General', 'Power Monitor', 'Application short name', 'authenticated', 40],
  ['general.organization_tagline', 'General', 'Local energy intelligence', 'Organization or site tagline', 'authenticated', 120],
  ['general.browser_title_prefix', 'General', 'Power Monitor', 'Browser-title prefix', 'authenticated', 60],
  ['login.heading', 'Login Screen', 'Sign in to your dashboard', 'Login heading', 'public', 160],
  ['login.subtitle', 'Login Screen', 'Use your local Power Monitor account to continue.', 'Login subtitle', 'public', 240],
  ['login.email_label', 'Login Screen', 'Email address', 'Email field label', 'public', 60],
  ['login.password_label', 'Login Screen', 'Password', 'Password field label', 'public', 60],
  ['login.sign_in_button', 'Login Screen', 'Sign in', 'Sign-in button label', 'public', 60],
  ['navigation.overview', 'Navigation', 'Overview', 'Overview', 'authenticated', 40],
  ['navigation.users_access', 'Navigation', 'Users & Access', 'Users & Access', 'authenticated', 60],
  ['pages.overview.title', 'Page Titles & Subtitles', 'Power Dashboard', 'Overview title', 'authenticated', 160],
  ['footer.dashboard', 'Footer & Support', 'Power Monitor Server', 'Dashboard footer text', 'authenticated', 160],
].map(([key, section, defaultValue, label, visibility, maxLength]) => ({ key, section, default: defaultValue, label, description: `${String(label)} description`, field_type: 'text', required: true, visibility, max_length: Number(maxLength), min_length: 1, line_breaks: false, url_companion: false, markdown: false, blank_allowed: false, preview_location: 'dashboard', current_value: defaultValue, published_revision: 0 }))

const statusZones = ['top_bar', 'overview_summary', 'workspace_header', 'page_summary', 'mobile_status_drawer', 'administration_diagnostics']
const statusPages = ['overview', 'devices', 'device_detail', 'topology', 'usage', 'history', 'costs', 'rates', 'rate_sources', 'alerts', 'enrollment', 'administration', 'system_health', 'backups']
const statusDefinition = (key: string, label: string, category: string, zone: string, order: number, pages = statusPages, permission = 'overview.view', criticalFallback?: string) => ({
  key, default_label: label, description: `${label} status from existing server data.`, category, data_source: key, current_value_schema: { status: 'string', display_value: 'string' }, severity_capability: ['info', 'success', 'warning', 'critical', 'unknown'], default_enabled: true, default_zone: zone, allowed_zones: statusZones, default_order: order, supported_pages: pages, global_shell_support: zone === 'top_bar', minimum_display_width: 140, preferred_display_width: 220, presentations: ['compact', 'standard', 'detailed'], icon_supported: true, label_supported: true, value_supported: true, freshness_supported: true, role_visibility_supported: true, permission_required: permission, configurable: true, critical_fallback: criticalFallback, renderer: key.includes('power') || key.includes('peak') ? 'power' : key.includes('count') || key.includes('online') || key.includes('offline') ? 'count' : 'health', icon: key.includes('alert') ? 'bell' : key.includes('power') ? 'zap' : 'activity', metric_identity: ({ 'data.live_connection': 'data.live_state', 'data.current_power': 'power.current', 'alerts.active_count': 'alerts.active_count', 'device.online_count': 'devices.online', 'device.offline_count': 'devices.offline', 'device.synchronized_count': 'data.synchronization', 'data.energy_today': 'energy.today', 'data.recent_peak': 'power.recent_peak', 'data.aggregate_coverage': 'data.coverage' } as Record<string, string>)[key] ?? key, canonical_priority: 300, allow_duplicate: false, suppress_when_empty: true, hide_in_zero_data_state: false, diagnostics_only: key.startsWith('system.'), registry_version: 'status-indicators/1.0',
})
const statusDefinitions = [
  statusDefinition('data.live_connection', 'Live data', 'Live data', 'top_bar', 10, statusPages, 'overview.view', 'Device disconnect alerts remain active.'),
  statusDefinition('data.current_power', 'Current load', 'Live data', 'top_bar', 20),
  statusDefinition('alerts.active_count', 'Active alerts', 'Alerts', 'top_bar', 30, statusPages, 'alerts.view', 'Alerts remain on Alerts & Notifications.'),
  statusDefinition('alerts.critical_count', 'Critical alerts', 'Alerts', 'page_summary', 10, ['alerts'], 'alerts.view', 'Critical alerts remain in the timeline.'),
  statusDefinition('alerts.warning_count', 'Warning alerts', 'Alerts', 'page_summary', 20, ['alerts'], 'alerts.view'),
  statusDefinition('alerts.enabled_rule_count', 'Rules enabled', 'Alerts', 'page_summary', 30, ['alerts'], 'alerts.view'),
  statusDefinition('alerts.disconnect_rule_state', 'Disconnect alerts', 'Alerts', 'page_summary', 40, ['alerts'], 'alerts.view', 'Heartbeat monitoring remains active.'),
  statusDefinition('device.online_count', 'Devices online', 'Devices', 'page_summary', 10, ['overview', 'devices'], 'devices.view', 'Device status remains on Devices.'),
  statusDefinition('device.offline_count', 'Offline or stale', 'Devices', 'page_summary', 20, ['overview', 'devices'], 'devices.view', 'Disconnect alerts remain active.'),
  statusDefinition('device.synchronized_count', 'Synchronized', 'Devices', 'page_summary', 50, ['overview', 'devices'], 'devices.view'),
  statusDefinition('data.energy_today', 'Energy today', 'Energy', 'overview_summary', 10, ['overview']),
  statusDefinition('cost.today', 'Estimated today', 'Costs', 'overview_summary', 20, ['overview']),
  statusDefinition('energy.billing_cycle', 'Billing-cycle energy', 'Energy', 'overview_summary', 30, ['overview']),
  statusDefinition('cost.billing_cycle_estimate', 'Cycle estimate', 'Costs', 'overview_summary', 40, ['overview']),
  statusDefinition('rate.current_plan', 'Current rate plan', 'Rates', 'page_summary', 30, ['rates'], 'rates.view'),
  statusDefinition('rate.current_period', 'Current rate period', 'Rates', 'page_summary', 20, ['rates'], 'rates.view'),
  statusDefinition('rate.current_price', 'Current energy price', 'Rates', 'page_summary', 25, ['rates'], 'rates.view'),
  statusDefinition('rate.source_health', 'Rate source health', 'Rates', 'page_summary', 10, ['rates', 'rate_sources'], 'rates.view'),
  statusDefinition('rate.update_pending', 'Rate update pending', 'Rates', 'page_summary', 20, ['rates', 'rate_sources'], 'rates.view'),
  statusDefinition('rate.last_successful_check', 'Last source check', 'Rates', 'page_summary', 10, ['rates', 'rate_sources'], 'rates.manage_sources'),
  statusDefinition('rate.next_scheduled_check', 'Next source check', 'Rates', 'page_summary', 20, ['rates', 'rate_sources'], 'rates.manage_sources'),
  statusDefinition('rate.review_policy', 'Review policy', 'Rates', 'page_summary', 30, ['rates', 'rate_sources'], 'rates.manage_sources'),
  statusDefinition('data.recent_peak', 'Recent peak', 'Energy', 'page_summary', 30, ['overview', 'history']),
  statusDefinition('rate.current_context', 'Rate context', 'Rates', 'page_summary', 35, ['overview', 'history'], 'rates.view'),
  statusDefinition('system.api_health', 'API', 'System', 'administration_diagnostics', 10, ['system_health'], 'settings.view'),
  statusDefinition('system.database_health', 'Database', 'System', 'administration_diagnostics', 20, ['system_health'], 'settings.view'),
  statusDefinition('system.worker_health', 'Worker', 'System', 'administration_diagnostics', 30, ['system_health'], 'settings.view', 'Worker alerts and diagnostics remain active.'),
]
const defaultStatusConfiguration = () => ({
  schema_version: 'power-monitor-status-layout/1.0' as const,
  registry_version: 'status-indicators/1.0', personalization_enabled: false as const,
  items: [
    ...statusDefinitions.map((definition) => ({ indicator_key: definition.key, page: '*', role: '*', breakpoint: 'default', visible: !['data.recent_peak'].includes(definition.key), zone: definition.default_zone, order: definition.default_order, density: 'standard', show_icon: true, show_label: true, show_value: true, show_freshness: true, show_severity: true, show_tooltip: true })),
    { indicator_key: 'data.live_connection', page: 'overview', role: '*', breakpoint: 'default', visible: true, zone: 'overview_summary', order: 20, density: 'compact' },
    { indicator_key: 'data.current_power', page: 'overview', role: '*', breakpoint: 'default', visible: false, zone: 'top_bar', order: 20, density: 'standard' },
    { indicator_key: 'device.online_count', page: 'overview', role: '*', breakpoint: 'default', visible: true, zone: 'overview_summary', order: 10, density: 'compact' },
    { indicator_key: 'device.offline_count', page: 'overview', role: '*', breakpoint: 'default', visible: true, zone: 'overview_summary', order: 30, density: 'compact' },
    { indicator_key: 'device.synchronized_count', page: 'overview', role: '*', breakpoint: 'default', visible: true, zone: 'overview_summary', order: 50, density: 'standard' },
    { indicator_key: 'alerts.active_count', page: 'overview', role: '*', breakpoint: 'default', visible: true, zone: 'overview_summary', order: 60, density: 'standard' },
    { indicator_key: 'rate.current_context', page: 'overview', role: '*', breakpoint: 'default', visible: false, zone: 'overview_summary', order: 70, density: 'standard' },
  ],
})

function statusDisplayValue(key: string): string {
  return {
    'data.current_power': '960 W',
    'alerts.active_count': '1',
    'device.online_count': '1',
    'device.offline_count': '0',
    'data.energy_today': '12.50 kWh',
    'cost.today': '$4.25',
    'energy.billing_cycle': '244 kWh',
    'cost.billing_cycle_estimate': '$83.11',
    'device.synchronized_count': '1/1',
    'rate.current_plan': 'Upland SCE account',
    'rate.current_period': 'On-peak',
    'rate.current_price': '$0.58/kWh',
    'data.recent_peak': '1,800 W',
    'rate.current_context': 'TOU-D-4-9PM · On-peak · $0.58/kWh',
  }[key] ?? 'Healthy'
}

const statusValues = Object.fromEntries(statusDefinitions.map((definition) => [definition.key, { status: 'healthy', severity: 'success', display_value: statusDisplayValue(definition.key), detail: `${definition.default_label} detail`, freshness_at: '2026-07-20T19:05:00Z' }]))

function resolveMockStatus(configuration: ReturnType<typeof defaultStatusConfiguration>, pageName: string, breakpoint: string, role = 'admin') {
  const items = statusDefinitions.flatMap((definition) => {
    if (definition.diagnostics_only && pageName !== 'system_health') return []
    if (!definition.global_shell_support && !definition.supported_pages.includes(pageName)) return []
    const matches = configuration.items.filter((item) => item.indicator_key === definition.key && (item.page === '*' || item.page === pageName) && (item.role === '*' || item.role === role) && (item.breakpoint === 'default' || item.breakpoint === breakpoint))
    const state = matches.reduce((value, item) => ({ ...value, ...item }), configuration.items.find((item) => item.indicator_key === definition.key) ?? { visible: definition.default_enabled, zone: definition.default_zone, order: definition.default_order })
    if (!state.visible) return []
    let zone = state.zone
    if (breakpoint === 'mobile' && !matches.some((item) => item.breakpoint === 'mobile' && item.zone) && zone !== 'top_bar') zone = 'mobile_status_drawer'
    return [{ ...state, indicator_key: definition.key, zone, definition }]
  })
  return { schema_version: 'power-monitor-status-layout/1.0', registry_version: 'status-indicators/1.0', published_revision: 1, page: pageName, roles: [role], breakpoint, zones: statusZones.map((key) => ({ key, items: items.filter((item) => item.zone === key).sort((a, b) => Number(a.order) - Number(b.order)) })).filter((zone) => zone.items.length), warnings: [], personalization_enabled: false }
}

async function mockApplication(page: Page, roles: string[] = ['admin'], initiallyAuthenticated = true) {
  let authenticated = initiallyAuthenticated
  let enrollmentCounter = 0
  let sensorRemoved = false
  let customDocument: Record<string, unknown> | undefined
  let customVersionStatus = 'draft'
  let candidateStatus = 'pending_review'
  let jobPolls = 0
  let userAccessRevision = 1
  let managedUserRoles = ['viewer']
  let managedUserStatus: 'active' | 'disabled' | 'removed' = 'active'
  let removedUserRoles = ['viewer']
  const managedUserPayload = (detail = false) => {
    const removed = managedUserStatus === 'removed'
    const activeRoles = removed ? [] : managedUserRoles
    const permissions = activeRoles.includes('operator') ? ['overview.view', 'sites.view', 'devices.view', 'devices.manage'] : activeRoles.length ? ['overview.view', 'sites.view', 'devices.view'] : []
    return {
      id: 'user-2',
      email: 'viewer@example.test',
      display_name: 'Dashboard Viewer',
      roles: activeRoles,
      is_active: managedUserStatus === 'active',
      status: managedUserStatus,
      all_sites: false,
      sites: removed ? [] : [site],
      site_ids: removed ? [] : ['site-1'],
      permissions,
      permission_count: permissions.length,
      mfa_enabled: false,
      last_login_at: '2026-07-19T10:00:00Z',
      active_session_count: removed ? 0 : 2,
      created_at: '2026-07-02T00:00:00Z',
      access_revision: userAccessRevision,
      protected_administrator: false,
      protected_account: false,
      removed_at: removed ? '2026-07-24T09:00:00Z' : undefined,
      removal_reason: removed ? 'Contract ended' : undefined,
      former_access: removed ? { roles: removedUserRoles, all_sites: false, site_ids: ['site-1'] } : undefined,
      ...(detail ? { sessions: removed ? [] : [{ id: 'session-2', created_at: '2026-07-20T08:00:00Z', last_seen_at: '2026-07-20T10:00:00Z', expires_at: '2026-07-21T10:00:00Z', source_ip: '192.168.0.20', user_agent: 'Browser' }], permission_sources: removed ? {} : { viewer: ['overview.view', 'sites.view', 'devices.view'] } } : {}),
    }
  }
  let textRevision = 0
  let textDraftRevision = 0
  let textPreviewedRevision = 0
  let textDraft: Record<string, string> = {}
  let publishedText: Record<string, string> = {}
  let rateConfiguration = { enabled: true, schedule_cron: '15 3 * * 0', timezone: 'America/Los_Angeles', jitter_minutes: 20, approval_mode: 'manual_review', auto_activate_verified: false, next_scheduled_run: '2026-07-26T10:15:00Z' }
  let rateSources: Array<{ id: string; name: string; url: string; parser_id: string; effective_from?: string; enabled: boolean; last_success_at?: string; consecutive_failures: number }> = [{ id: 'source-1', name: 'SCE public TOU page', url: 'https://www.sce.com/save-money/rates-financing/residential-rate-plans/time-of-use-plans', parser_id: 'sce_public_tou_html_v1', effective_from: '2026-06-01', enabled: true, last_success_at: '2026-07-19T10:15:00Z', consecutive_failures: 0 }]
  let statusRevision = 1
  let statusPublished = defaultStatusConfiguration()
  let statusDraft = structuredClone(statusPublished)
  let statusDraftRevision = 0
  let statusPreviewedRevision = 0
  let utilityAccounts: Array<Record<string, unknown>> = []
  let networkPolicies = [
    { id: 'ingress-policy-1', site_id: site.id, site_name: site.name, direction: 'device_ingress', mode: 'legacy_authenticated_any', revision: 1, migration_notice_pending: true, migrated_from_legacy: true, effective_summary: 'Legacy signed ingress · review required', cidrs: [] as Array<Record<string, unknown>> },
    { id: 'pull-policy-1', site_id: site.id, site_name: site.name, direction: 'server_pull', mode: 'deny_all', revision: 1, migration_notice_pending: true, migrated_from_legacy: true, effective_summary: 'Device network access denied', cidrs: [] as Array<Record<string, unknown>> },
  ]
  const dependencySummary = (
    siteId: string,
    revision: number,
    state: SiteDependencySummary['state'],
    isDefault: boolean,
    withResources = false,
  ): SiteDependencySummary => ({
    site_id: siteId,
    revision,
    state,
    default_site: isDefault,
    active: {
      sensors: withResources ? [{ id: 'device-garage', name: 'Garage meter', status: 'online', latest_reading_at: '2026-07-20T19:05:00Z' }] : [],
      utility_accounts: withResources ? [{ id: 'account-garage', name: 'Garage electric', status: 'active' }] : [],
      users: withResources ? [{ id: 'user-2', email: 'viewer@example.test', display_name: 'Dashboard Viewer' }] : [],
      alerts: withResources ? 1 : 0,
      enrollment_tokens: 0,
      jobs: 0,
    },
    retained: {
      raw_readings: withResources ? 1440 : 0,
      history_start: withResources ? '2026-07-01T00:00:00Z' : undefined,
      history_end: withResources ? '2026-07-20T19:05:00Z' : undefined,
      circuits: withResources ? 1 : 0,
      billing_cycles: withResources ? 1 : 0,
      alerts: withResources ? 2 : 0,
      audit_history: true,
      costs_and_rate_assignments: withResources,
    },
    required_actions: withResources
      ? [
          { resource: 'sensors', count: 1, actions: ['transfer', 'archive'] },
          { resource: 'utility_accounts', count: 1, actions: ['transfer', 'archive'] },
          { resource: 'users', count: 1, actions: ['end_access'] },
        ]
      : [],
    blockers: isDefault ? [{ code: 'default_site', message: 'Select another default site before removal.' }] : [],
    resolved: !withResources && !isDefault,
  })
  const adminSite = (
    id: string,
    name: string,
    code: string,
    isDefault: boolean,
    withResources = false,
  ): AdminSite => {
    const dependencies = dependencySummary(id, 1, 'active', isDefault, withResources)
    return {
      id,
      name,
      code,
      description: withResources ? 'Detached garage monitoring boundary.' : 'Primary monitored property.',
      location_label: withResources ? 'Garage' : 'Upland',
      organization: 'Power Monitor',
      timezone: 'America/Los_Angeles',
      currency: 'USD',
      locale: 'en-US',
      unit_system: 'imperial',
      allowed_cidrs: [],
      allowed_domains: [],
      allow_public_polling: false,
      lifecycle_state: 'active',
      is_default: isDefault,
      revision: 1,
      created_at: '2026-07-01T00:00:00Z',
      updated_at: '2026-07-20T19:05:00Z',
      sensor_count: dependencies.active.sensors.length,
      utility_account_count: dependencies.active.utility_accounts.length,
      assigned_user_count: dependencies.active.users.length,
      active_alert_count: dependencies.active.alerts,
      latest_reading_at: dependencies.retained.history_end,
      configuration_health: withResources ? 'ready' : 'warning',
      network_policy_summary: 'Signed private ingress; server pull denied',
      network_policies: [],
      dependencies,
    }
  }
  let managedSites: AdminSite[] = [
    adminSite('site-1', 'Upland Site', 'upland-site', true),
    adminSite('site-garage', 'Garage Site', 'garage-site', false, true),
  ]
  const siteAudits = new Map<string, Array<{ id: string; occurred_at: string; action: string; outcome: string; actor_id: string; details: Record<string, unknown> }>>(
    managedSites.map((value) => [value.id, [{
      id: `audit-${value.id}-created`,
      occurred_at: value.created_at,
      action: 'site.created',
      outcome: 'success',
      actor_id: 'user-1',
      details: { revision: 1 },
    }]]),
  )
  const replaceManagedSite = (updated: AdminSite) => {
    managedSites = managedSites.map((value) => value.id === updated.id ? updated : value)
    return updated
  }
  const recordSiteAudit = (siteId: string, action: string, revision: number) => {
    siteAudits.set(siteId, [{
      id: `audit-${siteId}-${action}-${revision}`,
      occurred_at: '2026-07-24T19:05:00Z',
      action,
      outcome: 'success',
      actor_id: 'user-1',
      details: { revision },
    }, ...(siteAudits.get(siteId) ?? [])])
  }
  const statusRevisions = [{ id: 'status-revision-1', revision: 1, registry_version: 'status-indicators/1.0', created_by: 'user-1', created_at: '2026-07-20T10:00:00Z', reason: 'Compiled current layout' }]
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    let body: unknown = []
    if (path === '/api/v1/auth/session') body = authenticated ? session(roles) : { authenticated: false, bootstrap_required: false }
    else if (path === '/api/v1/auth/login') { authenticated = true; body = session(roles) }
    else if (path === '/api/v1/auth/reauthenticate') body = { reauthenticated: true, valid_for_seconds: 300 }
    else if (path === '/api/v1/interface-text') body = { revision: textRevision, values: publishedText }
    else if (path === '/api/v1/status-indicators/registry') body = { registry_version: 'status-indicators/1.0', indicators: statusDefinitions, zones: statusZones, pages: statusPages, breakpoints: ['desktop', 'tablet', 'mobile'] }
    else if (path === '/api/v1/status-indicators/layout') body = resolveMockStatus(statusPublished, url.searchParams.get('page') ?? 'overview', url.searchParams.get('breakpoint') ?? 'desktop', roles[0] ?? 'viewer')
    else if (path === '/api/v1/status-indicators/values') body = { registry_version: 'status-indicators/1.0', generated_at: '2026-07-20T19:05:00Z', values: statusValues }
    else if (path === '/api/v1/admin/status-indicators/catalog') body = { registry_version: 'status-indicators/1.0', schema_version: 'power-monitor-status-layout/1.0', published_revision: statusRevision, indicators: statusDefinitions, zones: statusZones, pages: statusPages, breakpoints: ['desktop', 'tablet', 'mobile'], roles: [{ id: 'admin', label: 'Administrator' }, { id: 'operator', label: 'Operator' }, { id: 'viewer', label: 'Read-Only Viewer' }], new_indicator_keys: [], excluded_status_surfaces: [{ surface: 'record_row_status', reason: 'Record states stay attached to their records.' }, { surface: 'functional_feedback', reason: 'Validation and operation errors remain mandatory.' }, { surface: 'site_selector', reason: 'The site selector remains an authorization scope control.' }] }
    else if (path === '/api/v1/admin/status-indicators/draft' && request.method() === 'GET') body = { exists: statusDraftRevision > 0, base_revision: statusRevision, draft_revision: statusDraftRevision, previewed_revision: statusPreviewedRevision || undefined, configuration: statusDraft, critical_hidden: statusDraft.items.filter((item) => !item.visible && statusDefinitions.find((definition) => definition.key === item.indicator_key)?.critical_fallback).map((item) => ({ indicator_key: item.indicator_key, fallback: 'Related workflow remains active.' })) }
    else if (path === '/api/v1/admin/status-indicators/draft' && request.method() === 'PUT') { const update = JSON.parse(request.postData() ?? '{}') as { configuration: ReturnType<typeof defaultStatusConfiguration> }; statusDraft = update.configuration; statusDraftRevision += 1; statusPreviewedRevision = 0; body = { exists: true, base_revision: statusRevision, draft_revision: statusDraftRevision, configuration: statusDraft, critical_hidden: [] } }
    else if (path === '/api/v1/admin/status-indicators/draft' && request.method() === 'DELETE') { statusDraft = structuredClone(statusPublished); statusDraftRevision = 0; statusPreviewedRevision = 0; await route.fulfill({ status: 204, body: '' }); return }
    else if (path === '/api/v1/admin/status-indicators/validate') body = { valid: true, registry_version: 'status-indicators/1.0', item_count: statusDraft.items.length, warnings: [], critical_hidden: [] }
    else if (path === '/api/v1/admin/status-indicators/repair') body = { configuration: statusDraft, repairs: [], warnings: [], message: 'Recommended placements are ready to review.' }
    else if (path === '/api/v1/admin/status-indicators/preview') {
      const previewRequest = JSON.parse(request.postData() ?? '{}') as { configuration?: ReturnType<typeof defaultStatusConfiguration>; page: string; role: string; breakpoint: string; scenario?: string }
      const previewConfiguration = structuredClone(previewRequest.configuration ?? statusDraft)
      const firstItem = previewConfiguration.items[0]
      const secondItem = previewConfiguration.items[1]
      if (previewRequest.scenario === 'one_disabled' && firstItem) firstItem.visible = false
      if (previewRequest.scenario === 'two_disabled') {
        if (firstItem) firstItem.visible = false
        if (secondItem) secondItem.visible = false
      }
      if (previewRequest.scenario === 'one_only') previewConfiguration.items.forEach((item, index) => { item.visible = index === 0 })
      if (previewRequest.scenario === 'empty_zone' && firstItem) previewConfiguration.items.filter((item) => item.zone === firstItem.zone).forEach((item) => { item.visible = false })
      statusPreviewedRevision = statusDraftRevision
      const previewLayout = resolveMockStatus(previewConfiguration, previewRequest.page, previewRequest.breakpoint, previewRequest.role)
      const firstPreviewItem = previewLayout.zones[0]?.items[0]
      if (previewRequest.scenario === 'long_label' && firstPreviewItem) {
        Object.assign(firstPreviewItem, {
          show_label: true,
          density: 'standard',
          definition: { ...firstPreviewItem.definition, default_label: 'A deliberately long translated indicator label that wraps safely' },
        })
      }
      body = { layout: previewLayout, values: statusValues, warnings: [] }
    }
    else if (path === '/api/v1/admin/status-indicators/publish') { statusPublished = structuredClone(statusDraft); statusRevision += 1; statusRevisions.unshift({ id: `status-revision-${statusRevision}`, revision: statusRevision, registry_version: 'status-indicators/1.0', created_by: 'user-1', created_at: '2026-07-20T19:10:00Z', reason: 'Published layout' }); statusDraftRevision = 0; statusPreviewedRevision = 0; body = { ...statusRevisions[0], configuration: statusPublished } }
    else if (path === '/api/v1/admin/status-indicators/revisions') body = { revisions: statusRevisions }
    else if (path.match(/^\/api\/v1\/admin\/status-indicators\/revisions\/[^/]+\/restore$/)) { statusRevision += 1; statusPublished = defaultStatusConfiguration(); statusDraft = structuredClone(statusPublished); body = { id: `status-revision-${statusRevision}`, revision: statusRevision, restored_from_id: path.split('/').at(-2), configuration: statusPublished } }
    else if (path === '/api/v1/admin/status-indicators/reset') { statusDraft = defaultStatusConfiguration(); statusDraftRevision += 1; statusPreviewedRevision = 0; body = { exists: true, base_revision: statusRevision, draft_revision: statusDraftRevision, configuration: statusDraft, critical_hidden: [] } }
    else if (path === '/api/v1/admin/status-indicators/export') body = { schema_version: 'power-monitor-status-layout/1.0', registry_version: 'status-indicators/1.0', published_revision: statusRevision, configuration: url.searchParams.get('draft') === 'true' ? statusDraft : statusPublished }
    else if (path === '/api/v1/admin/status-indicators/import') { const document = JSON.parse(request.postData() ?? '{}') as { configuration: ReturnType<typeof defaultStatusConfiguration> }; statusDraft = document.configuration; statusDraftRevision += 1; statusPreviewedRevision = 0; body = { exists: true, base_revision: statusRevision, draft_revision: statusDraftRevision, configuration: statusDraft, critical_hidden: [], requires_preview: true, difference_summary: [] } }
    else if (path === '/api/v1/public/interface-text') body = { revision: textRevision, values: Object.fromEntries(Object.entries(publishedText).filter(([key]) => textDefinitions.some((definition) => definition.key === key && definition.visibility === 'public'))) }
    else if (path === '/api/v1/admin/permissions') body = { permissions: accessPermissions, dependencies: { 'users.manage': ['users.view'], 'roles.manage': ['roles.view'], 'interface_text.manage': ['interface_text.view'] } }
    else if (path === '/api/v1/admin/roles') body = { roles: [
      { id: 'admin', display_name: 'Administrator', description: 'Full application administration', built_in: true, archived: false, revision: 1, permissions: accessPermissions.map((item) => item.code), assigned_user_count: 1, created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-01T00:00:00Z' },
      { id: 'viewer', display_name: 'Read-Only Viewer', description: 'Read-only assigned-site access', built_in: true, archived: false, revision: 1, permissions: ['overview.view', 'sites.view', 'devices.view'], assigned_user_count: 1, created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-01T00:00:00Z' },
      { id: 'operator', display_name: 'Operator', description: 'Assigned-site operations', built_in: true, archived: false, revision: 1, permissions: ['overview.view', 'sites.view', 'devices.view', 'devices.manage'], assigned_user_count: 0, created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-01T00:00:00Z' },
    ] }
    else if (path === '/api/v1/admin/users' && request.method() === 'GET') body = { users: [
      { ...session(roles).user, is_active: true, status: 'active', all_sites: true, sites: [], site_ids: [], permissions: accessPermissions.map((item) => item.code), permission_count: accessPermissions.length, mfa_enabled: true, last_login_at: '2026-07-20T10:00:00Z', active_session_count: 1, created_at: '2026-07-01T00:00:00Z', access_revision: 1, protected_administrator: true, protected_account: true },
      managedUserPayload(),
    ] }
    else if (path === '/api/v1/admin/users/user-2' && request.method() === 'GET') body = managedUserPayload(true)
    else if (path === '/api/v1/admin/users/user-2/access' && request.method() === 'PUT') { const update = JSON.parse(request.postData() ?? '{}') as { role_ids: string[] }; managedUserRoles = update.role_ids; userAccessRevision += 1; body = { ...managedUserPayload(true), sessions_revoked: 2 } }
    else if (path === '/api/v1/admin/users/user-2/disable' && request.method() === 'POST') { managedUserStatus = 'disabled'; userAccessRevision += 1; body = { changed: true, sessions_revoked: 2, user: managedUserPayload() } }
    else if (path === '/api/v1/admin/users/user-2/enable' && request.method() === 'POST') { managedUserStatus = 'active'; userAccessRevision += 1; body = { changed: true, sessions_revoked: 0, user: managedUserPayload() } }
    else if (path === '/api/v1/admin/users/user-2/remove' && request.method() === 'POST') { removedUserRoles = [...managedUserRoles]; managedUserStatus = 'removed'; userAccessRevision += 1; body = { changed: true, sessions_revoked: 2, user: managedUserPayload() } }
    else if (path === '/api/v1/admin/users/user-2/restore' && request.method() === 'POST') { managedUserRoles = []; managedUserStatus = 'disabled'; userAccessRevision += 1; body = { changed: true, sessions_revoked: 0, user: managedUserPayload() } }
    else if (path === '/api/v1/admin/users/user-2/access-history') body = { events: [{ id: 'audit-1', occurred_at: '2026-07-20T10:00:00Z', actor_id: 'user-1', action: 'user.access_updated', outcome: 'success', details: { reason: 'Operational assignment' } }] }
    else if (path === '/api/v1/admin/users/user-2/revoke-sessions') body = { sessions_revoked: 2 }
    else if (path === '/api/v1/admin/interface-text/catalog') body = {
      revision: textRevision,
      definitions: textDefinitions.map((item) => {
        const key = String(item.key)
        return { ...item, current_value: publishedText[key] ?? item.default, current_override: publishedText[key], published_revision: textRevision }
      }),
    }
    else if (path === '/api/v1/admin/interface-text/draft' && request.method() === 'GET') body = { exists: textDraftRevision > 0, base_revision: textRevision, draft_revision: textDraftRevision, previewed_revision: textPreviewedRevision || undefined, values: textDraft }
    else if (path === '/api/v1/admin/interface-text/draft' && request.method() === 'PUT') { const update = JSON.parse(request.postData() ?? '{}') as { values: Record<string, string> }; textDraft = update.values; textDraftRevision += 1; textPreviewedRevision = 0; body = { exists: true, base_revision: textRevision, draft_revision: textDraftRevision, values: textDraft } }
    else if (path === '/api/v1/admin/interface-text/draft' && request.method() === 'DELETE') { textDraft = {}; textDraftRevision = 0; textPreviewedRevision = 0; await route.fulfill({ status: 204, body: '' }); return }
    else if (path === '/api/v1/admin/interface-text/preview') { textPreviewedRevision = textDraftRevision; body = { draft_revision: textDraftRevision, values: { ...publishedText, ...textDraft } } }
    else if (path === '/api/v1/admin/interface-text/publish') { publishedText = { ...publishedText, ...textDraft }; textRevision += 1; textDraft = {}; textDraftRevision = 0; textPreviewedRevision = 0; body = { id: `revision-${textRevision}`, revision: textRevision, values: publishedText, overrides: publishedText, changed_key_count: Object.keys(publishedText).length, created_at: '2026-07-20T10:00:00Z', created_by: 'user-1' } }
    else if (path === '/api/v1/admin/interface-text/revisions') body = { revisions: textRevision ? [{ id: `revision-${textRevision}`, revision: textRevision, created_by: 'user-1', created_at: '2026-07-20T10:00:00Z', reason: 'Dashboard update', changed_key_count: Object.keys(publishedText).length }] : [] }
    else if (path === '/api/v1/admin/sites' && request.method() === 'GET') {
      const status = url.searchParams.get('status') ?? 'current'
      const search = (url.searchParams.get('search') ?? '').toLowerCase()
      body = managedSites.filter((value) => {
        if (status === 'current' && value.lifecycle_state === 'removed') return false
        if (!['current', 'all'].includes(status) && value.lifecycle_state !== status) return false
        return !search || `${value.name} ${value.code} ${value.timezone} ${value.location_label ?? ''}`.toLowerCase().includes(search)
      })
    }
    else if (path === '/api/v1/admin/sites' && request.method() === 'POST') {
      const value = JSON.parse(request.postData() ?? '{}') as {
        name: string
        code: string
        description?: string
        location_label?: string
        organization?: string
        timezone: string
        currency: string
        locale: string
        unit_system: 'imperial' | 'metric'
        make_default: boolean
        create_utility_account_after: boolean
      }
      const id = `site-${managedSites.length + 1}`
      if (value.make_default) {
        managedSites = managedSites.map((current) => ({
          ...current,
          is_default: false,
          revision: current.revision + 1,
          dependencies: { ...current.dependencies, revision: current.revision + 1, default_site: false, blockers: [] },
        }))
      }
      const created: AdminSite = {
        ...adminSite(id, value.name, value.code, value.make_default),
        description: value.description,
        location_label: value.location_label,
        organization: value.organization,
        timezone: value.timezone,
        currency: value.currency,
        locale: value.locale,
        unit_system: value.unit_system,
      }
      managedSites = [...managedSites, created]
      siteAudits.set(id, [{
        id: `audit-${id}-created`,
        occurred_at: '2026-07-24T19:05:00Z',
        action: 'site.created',
        outcome: 'success',
        actor_id: 'user-1',
        details: { revision: 1 },
      }])
      body = { ...created, next_step: value.create_utility_account_after ? 'create_utility_account' : 'site_details' }
    }
    else if (path.match(/^\/api\/v1\/admin\/sites\/[^/]+$/) && request.method() === 'PUT') {
      const id = path.split('/').at(-1) ?? ''
      const current = managedSites.find((value) => value.id === id)
      const value = JSON.parse(request.postData() ?? '{}') as Partial<AdminSite>
      if (current) {
        const revision = current.revision + 1
        body = replaceManagedSite({
          ...current,
          ...value,
          revision,
          updated_at: '2026-07-24T19:05:00Z',
          dependencies: { ...current.dependencies, revision },
        })
        recordSiteAudit(id, 'site.updated', revision)
      }
    }
    else if (path.match(/^\/api\/v1\/admin\/sites\/[^/]+\/set-default$/) && request.method() === 'POST') {
      const id = path.split('/').at(-2) ?? ''
      managedSites = managedSites.map((current) => {
        const revision = current.revision + 1
        return {
          ...current,
          is_default: current.id === id,
          revision,
          dependencies: {
            ...current.dependencies,
            revision,
            default_site: current.id === id,
            blockers: current.id === id ? [{ code: 'default_site', message: 'Select another default site before removal.' }] : [],
            resolved: current.id !== id && current.dependencies.required_actions.length === 0,
          },
        }
      })
      const updated = managedSites.find((value) => value.id === id)
      if (updated) {
        recordSiteAudit(id, 'site.default_changed', updated.revision)
        body = updated
      }
    }
    else if (path.match(/^\/api\/v1\/admin\/sites\/[^/]+\/dependencies$/) && request.method() === 'GET') {
      body = managedSites.find((value) => value.id === path.split('/').at(-2))?.dependencies ?? {}
    }
    else if (path.match(/^\/api\/v1\/admin\/sites\/[^/]+\/transfer-resources$/) && request.method() === 'POST') {
      const id = path.split('/').at(-2) ?? ''
      const current = managedSites.find((value) => value.id === id)
      if (current) {
        const revision = current.revision + 1
        const dependencies: SiteDependencySummary = {
          ...current.dependencies,
          revision,
          active: { sensors: [], utility_accounts: [], users: [], alerts: 0, enrollment_tokens: 0, jobs: 0 },
          required_actions: [],
          resolved: !current.is_default,
        }
        const updated = replaceManagedSite({
          ...current,
          revision,
          sensor_count: 0,
          utility_account_count: 0,
          assigned_user_count: 0,
          active_alert_count: 0,
          dependencies,
        })
        recordSiteAudit(id, 'site.dependencies_resolved', revision)
        body = { site: updated, resolution: { sensors: 1, utility_accounts: 1, users: 1 } }
      }
    }
    else if (path.match(/^\/api\/v1\/admin\/sites\/[^/]+\/(disable|enable|remove|restore)$/) && request.method() === 'POST') {
      const values = path.split('/')
      const id = values.at(-2) ?? ''
      const action = values.at(-1) ?? ''
      const current = managedSites.find((value) => value.id === id)
      if (current) {
        const revision = current.revision + 1
        const lifecycleState = action === 'enable' ? 'active' : action === 'restore' ? 'disabled' : action === 'remove' ? 'removed' : 'disabled'
        const dependencies = dependencySummary(id, revision, lifecycleState, current.is_default, false)
        body = replaceManagedSite({
          ...current,
          lifecycle_state: lifecycleState,
          revision,
          is_default: action === 'remove' ? false : current.is_default,
          dependencies,
          removed_at: action === 'remove' ? '2026-07-24T19:05:00Z' : current.removed_at,
          restored_at: action === 'restore' ? '2026-07-24T19:05:00Z' : current.restored_at,
        })
        recordSiteAudit(id, `site.${action === 'enable' ? 'enabled' : action === 'disable' ? 'disabled' : action === 'remove' ? 'removed' : 'restored'}`, revision)
      }
    }
    else if (path.match(/^\/api\/v1\/admin\/sites\/[^/]+\/audit$/) && request.method() === 'GET') {
      body = siteAudits.get(path.split('/').at(-2) ?? '') ?? []
    }
    else if (path === '/api/v1/sites') {
      body = managedSites
        .filter((value) => value.lifecycle_state === 'active')
        .map((value) => ({
          id: value.id,
          name: value.name,
          code: value.code,
          description: value.description,
          location_label: value.location_label,
          organization: value.organization,
          timezone: value.timezone,
          currency: value.currency,
          locale: value.locale,
          unit_system: value.unit_system,
          allowed_cidrs: value.allowed_cidrs,
          allowed_domains: value.allowed_domains,
          allow_public_polling: value.allow_public_polling,
          lifecycle_state: value.lifecycle_state,
          is_default: value.is_default,
          revision: value.revision,
        }))
    }
    else if (path === '/api/v1/sites/site-1/setup-readiness') body = {
      monitoring: { state: 'no_sensors_enrolled', device_count: 0 },
      rate_and_cost: utilityAccounts.length
        ? { state: 'rate_configured_effective', cost_state: 'cost_calculation_blocked_missing_readings', account_count: utilityAccounts.length, effective_account_count: utilityAccounts.length, cost_ready_account_count: 0, pending_candidate_count: 0 }
        : { state: 'no_utility_account', cost_state: 'cost_calculation_blocked_invalid_configuration', account_count: 0, effective_account_count: 0, cost_ready_account_count: 0, pending_candidate_count: 0 },
    }
    else if (path === '/api/v1/admin/sites/site-1/utility-accounts' && request.method() === 'GET') body = utilityAccounts
    else if (path === '/api/v1/admin/sites/site-1/utility-accounts' && request.method() === 'POST') {
      const value = JSON.parse(request.postData() ?? '{}') as Record<string, unknown>
      const created = {
        id: `account-${utilityAccounts.length + 1}`, site_id: site.id, site_name: site.name,
        utility_id: 'utility-sce', utility_name: 'Southern California Edison', name: value.name,
        nickname: value.nickname, account_number_suffix: value.account_number_suffix, status: 'active',
        timezone: site.timezone, currency: 'USD', billing_cycle_start_day: value.billing_cycle_start_day,
        generation_provider: value.generation_provider, provider_mode: value.provider_mode, service_class: value.service_class,
        cost_scope: value.cost_scope, allocation_method: value.allocation_method, full_account_override: value.full_account_override,
        revision: 1, assignment_count: 1, device_count: 0,
        readiness: { rate: 'rate_configured_effective', cost: 'cost_blocked_missing_readings', topology_complete: false },
        rate_context: { state: 'rate_configured_effective', current_plan: officialPlan.name, plan_code: officialPlan.code, current_version: 1, rate_version_id: officialVersion.id, current_period: 'On Peak', current_price_per_kwh: '0.58000000', next_period: 'Off Peak', next_price_per_kwh: '0.24000000', next_period_at: '2026-07-21T04:00:00Z', current_currency: 'USD', assignment_effective_from: (value.rate_assignment as { effective_from: string }).effective_from },
      }
      utilityAccounts = [...utilityAccounts, created]
      body = created
    }
    else if (path === '/api/v1/rates/versions/rate-version-official/current-context') body = { current_period: 'On Peak', current_price_per_kwh: '0.58000000', next_period: 'Off Peak', next_price_per_kwh: '0.24000000', next_period_at: '2026-07-21T04:00:00Z', provider_mode: 'sce_bundled', account_adjustments: [{ name: 'Base service charge', component: 'daily_fixed_charge', value: '0.03', unit: 'per_day', scope: 'full_account_estimate' }] }
    else if (path === '/api/v1/admin/network/policies' && request.method() === 'GET') body = networkPolicies
    else if (path.match(/^\/api\/v1\/admin\/network\/policies\/[^/]+$/) && request.method() === 'PUT') {
      const id = path.split('/').at(-1)
      const value = JSON.parse(request.postData() ?? '{}') as { mode: string }
      networkPolicies = networkPolicies.map((policy) => policy.id === id ? { ...policy, mode: value.mode, revision: policy.revision + 1, migration_notice_pending: false, effective_summary: value.mode === 'allow_listed_private' ? `Listed private networks only · ${policy.cidrs.length} CIDR` : value.mode === 'allow_all_private' ? 'All private sensor networks allowed' : 'Device network access denied' } : policy)
      body = networkPolicies.find((policy) => policy.id === id)
    }
    else if (path === '/api/v1/admin/network/cidrs' && request.method() === 'POST') {
      const value = JSON.parse(request.postData() ?? '{}') as { policy_id: string; network: string; label: string; enabled: boolean }
      const normalized = value.network === '192.168.50.99/24' ? '192.168.50.0/24' : value.network
      const cidr = { id: 'cidr-1', policy_id: value.policy_id, network: normalized, label: value.label, enabled: value.enabled, revision: 1 }
      networkPolicies = networkPolicies.map((policy) => policy.id === value.policy_id ? { ...policy, revision: policy.revision + 1, cidrs: [...policy.cidrs, cidr] } : policy)
      body = { ...cidr, warnings: [] }
    }
    else if (path === '/api/v1/admin/network/test-address' && request.method() === 'POST') {
      const value = JSON.parse(request.postData() ?? '{}') as { policy_id: string; address: string }
      const policy = networkPolicies.find((item) => item.id === value.policy_id) ?? networkPolicies[0]
      const allowed = value.address.startsWith('192.168.50.') && policy?.mode === 'allow_listed_private'
      body = { allowed, address: value.address, direction: policy?.direction, mode: policy?.mode, matching_rule: allowed ? 'Sensor VLAN (192.168.50.0/24)' : null, reason: allowed ? 'Address matched an enabled private-network rule' : 'Address does not match the effective sensor network policy' }
    }
    else if (path === '/api/v1/admin/network/observed-devices') body = []
    else if (path === '/api/v1/admin/network/runtime') body = { sensor_server_url: 'https://power-monitor.test', tls_verification_required: true, certificate_trust: 'Internal CA', default_device_api_port: 443, communication_modes: ['push', 'pull', 'hybrid'], mdns_authoritative: false, heartbeat_expectation_seconds: 15, stale_device_seconds: 60, server_time: '2026-07-21T12:00:00Z', server_timezone: 'UTC', trusted_forwarded_headers: true, address_source: 'Signed device heartbeat' }
    else if (path === '/api/v1/fleet/summary') body = fleet
    else if (path === '/api/v1/devices') {
      const lifecycle = url.searchParams.get('lifecycle')
      if (lifecycle === 'decommissioned') body = sensorRemoved ? [{ ...device, status: 'decommissioned', lifecycle_status: 'decommissioned', circuit_id: undefined, circuit_name: undefined, decommissioned_at: '2026-07-20T07:00:00Z', decommissioned_by_name: 'Fleet Owner', decommission_reason: 'replaced', retained_history: true, re_enrollment_allowed: true }] : []
      else if (page.url().includes('/history')) body = [device, historyDevice]
      else body = sensorRemoved && lifecycle === 'active' ? [] : [device]
    }
    else if (path === '/api/v1/devices/device-1') body = { device: { ...device, hardware_id: 'esp32-garage-001' }, history: { reading_count: 1440, earliest_reading_at: '2026-06-20T06:00:00Z', latest_reading_at: '2026-07-20T06:00:00Z', retained: true }, lifecycle_events: [] }
    else if (path === '/api/v1/admin/devices/device-1/unclaim' && request.method() === 'POST') { sensorRemoved = true; body = { device_id: 'device-1', status: 'decommissioned', already_decommissioned: false, historical_data_retained: true } }
    else if (path === '/api/v1/events/stream') {
      await route.fulfill({ contentType: 'text/event-stream', body: 'event: fleet\ndata: {"type":"fleet","devices":[]}\n\n' })
      return
    } else if (path === '/api/v1/circuits') body = [{ id: 'main-1', site_id: 'site-1', name: 'Main panel', measurement_role: 'main' }, { id: 'branch-1', site_id: 'site-1', parent_id: 'main-1', name: 'Garage branch', measurement_role: 'branch' }, { id: 'leg-2', site_id: 'site-1', name: 'Main Panel L2', measurement_role: 'service-leg', split_phase_group: 'main-panel' }]
    else if (path === '/api/v1/aggregate-sets') body = [{ id: 'aggregate-1', site_id: 'site-1', name: 'Explicit home total', cost_scope: 'full_account', is_default: true, members: [{ circuit_id: 'main-1' }, { circuit_id: 'branch-1' }], overlap_confirmed_at: '2026-07-20T06:00:00Z' }]
    else if (path === '/api/v1/readings/history') body = { points: [{ timestamp: '2026-07-20T05:59:00Z', power_w: '900', quality_flags: [] }, { timestamp: '2026-07-20T06:00:00Z', power_w: '960', quality_flags: [] }], missing_ranges: [{ start_sequence: 2, end_sequence: 3 }], coverage_percent: '98.5' }
    else if (path === '/api/v1/history/query') {
      const query = JSON.parse(request.postData() ?? '{}') as { scope: { type: string; device_id?: string; device_ids?: string[] }; display_mode: string; selection_start_utc?: string; selection_end_utc?: string }
      const selectedIds = query.scope.type === 'devices' ? query.scope.device_ids ?? [] : [query.scope.device_id ?? 'device-1']
      const selected = [device, historyDevice].filter((item) => selectedIds.includes(item.id))
      const sensorCount = selected.length || 1
      const makePoint = (index: number, seriesId: string, seriesName: string, individual = false) => ({
        interval_start_utc: `2026-07-21T0${3 + index}:00:00Z`, interval_end_utc: `2026-07-21T0${4 + index}:00:00Z`,
        local_start: `2026-07-20T${20 + index}:00:00-07:00`, local_end: `2026-07-20T${21 + index}:00:00-07:00`, utc_offset: '-07:00',
        series_id: seriesId, series_name: seriesName, device_id: individual ? seriesId : undefined,
        included_sensor_count: individual ? 1 : sensorCount, contributing_sensor_count: individual ? 1 : sensorCount,
        energy_kwh: individual ? '1.000000' : String(sensorCount), average_power_w: individual ? '1000' : String(sensorCount * 1000), peak_power_w: individual ? '1200' : String(sensorCount * 1200),
        voltage_min_v: '119', voltage_avg_v: '120', voltage_max_v: '121', current_a: individual ? '8.333' : undefined, power_factor: '1', frequency_hz: '60',
        tou_period: 'on-peak', rate_per_kwh: '1.00000000', energy_cost: individual ? '1.000000' : String(sensorCount), rate_plan_name: 'Deterministic TOU plan', rate_version_id: 'rate-version-history', rate_effective_from: '2026-06-01', mixed_rates: false,
        coverage_percent: index === 0 ? '100' : '98.5', missing_sensor_ids: [], quality_flags: index === 0 ? [] : ['partial_coverage'],
        rate_contributions: [{ utility_account_id: 'account-1', rate_plan_id: 'rate-plan-history', rate_plan_name: 'Deterministic TOU plan', rate_version_id: 'rate-version-history', rate_version: 3, rate_effective_from: '2026-06-01', tou_period: 'on-peak', energy_kwh: individual ? '1' : String(sensorCount), rate_per_kwh: '1', energy_cost: individual ? '1' : String(sensorCount) }],
      })
      const combined = [0, 1].map((index) => makePoint(index, 'combined', selected.map((item) => item.name).join(' + ') || device.name))
      const summary = { start_utc: '2026-07-21T03:00:00Z', end_utc: '2026-07-21T05:00:00Z', energy_kwh: String(sensorCount * 2), energy_cost: String(sensorCount * 2), blended_rate_per_kwh: '1', average_power_w: String(sensorCount * 1000), peak_power_w: String(sensorCount * 1200), highest_cost_bucket_start: '2026-07-21T03:00:00Z', highest_cost_bucket_value: String(sensorCount), highest_usage_bucket_start: '2026-07-21T03:00:00Z', highest_usage_bucket_kwh: String(sensorCount), coverage_percent: '99.25', contributing_sensor_count: sensorCount, tou_breakdown: { 'on-peak': { energy_kwh: String(sensorCount * 2), energy_cost: String(sensorCount * 2) } } }
      body = {
        scope: { type: query.scope.type, display_name: selected.map((item) => item.name).join(' + ') || device.name, site_id: 'site-1', site_name: 'Upland Site', timezone: 'America/Los_Angeles', included_device_ids: selectedIds, included_device_names: selected.map((item) => item.name), excluded_device_ids: [], mixed_rates: false },
        display_mode: query.display_mode, metrics: ['energy_kwh', 'energy_cost'], bucket: '1h', summary,
        selected_summary: query.selection_start_utc && query.selection_end_utc ? summary : undefined,
        combined: query.display_mode === 'individual' ? [] : combined,
        individual: query.display_mode === 'combined' ? [] : selected.map((item) => ({ device_id: item.id, name: item.name, circuit_name: item.circuit_name, status: item.status, points: [0, 1].map((index) => makePoint(index, item.id, item.name, true)) })),
        rate_versions_used: [{ rate_plan_id: 'rate-plan-history', rate_plan_name: 'Deterministic TOU plan', rate_version_id: 'rate-version-history', rate_version: 3, effective_from: '2026-06-01' }], warnings: [], total_buckets: 2, page: 1, page_size: 250,
      }
    }
    else if (path === '/api/v1/history/export') {
      await route.fulfill({ contentType: 'text/csv', headers: { 'Content-Disposition': 'attachment; filename=power-monitor-history.csv' }, body: 'power-monitor-history-export/1.0\ninterval_energy_cost\n4.00\n' })
      return
    }
    else if (path === '/api/v1/alerts') body = [{ id: 'alert-1', name: 'Synchronization backlog', status: 'active', severity: 'warning', device_id: 'device-1', opened_at: '2026-07-20T06:00:00Z', evidence: { backlog: 42 } }]
    else if (path === '/api/v1/utility-accounts') body = [{ id: 'account-1', name: 'Home utility account' }]
    else if (path === '/api/v1/rates/plans' && request.method() === 'GET') body = [
      officialPlan,
      ...(customDocument ? [{ id: 'custom-plan-1', code: customDocument.plan_code, name: customDocument.plan_name, description: customDocument.description, plan_kind: 'custom', ownership_scope: customDocument.ownership_scope, currency: 'USD', timezone: 'America/Los_Angeles', status: customVersionStatus === 'active' ? 'active' : 'draft', versions: [{ ...officialVersion, id: 'custom-version-1', status: customVersionStatus, source_kind: 'custom', source_label: 'Administrator-defined rate plan', integrity_sha256: 'b'.repeat(64), is_active: customVersionStatus === 'active', immutable: customVersionStatus === 'active' }] }] : []),
    ]
    else if (path === '/api/v1/rates/plans' && request.method() === 'POST') {
      customDocument = JSON.parse(request.postData() ?? '{}') as Record<string, unknown>
      body = { plan: { id: 'custom-plan-1', versions: [{ ...officialVersion, id: 'custom-version-1', status: 'draft', is_active: false, immutable: false }] }, document: customDocument }
    }
    else if (path === '/api/v1/rates/versions/rate-version-official' && request.method() === 'GET') body = {
      version: officialVersion,
      document: {
        ...emptyRateDocument(),
        plan_name: officialPlan.name,
        plan_code: officialPlan.code,
        description: officialPlan.description,
        source_label: officialVersion.source_label,
        provider_mode: 'sce_delivery_generation',
      },
    }
    else if (path === '/api/v1/rates/versions/custom-version-1' && request.method() === 'GET') body = { version: { ...officialVersion, id: 'custom-version-1', status: customVersionStatus, is_active: customVersionStatus === 'active', immutable: customVersionStatus === 'active' }, document: customDocument }
    else if (path === '/api/v1/rates/versions/custom-version-1' && request.method() === 'PATCH') { customDocument = JSON.parse(request.postData() ?? '{}') as Record<string, unknown>; body = { version: { id: 'custom-version-1', status: 'draft' }, validation: { valid: true, errors: [], warnings: [], integrity_sha256: 'b'.repeat(64), coverage: { 'all-year/all-days': true } } } }
    else if (path === '/api/v1/rates/validate-document') body = { valid: true, errors: [], warnings: [], integrity_sha256: 'b'.repeat(64), coverage: { 'all-year/all-days': true } }
    else if (path === '/api/v1/rates/versions/custom-version-1/activate') { customVersionStatus = 'active'; body = { status: 'active', version: { id: 'custom-version-1', status: 'active' }, validation: { valid: true, errors: [], warnings: [], integrity_sha256: 'b'.repeat(64), coverage: { 'all-year/all-days': true } } } }
    else if (path === '/api/v1/rates/assignments' && request.method() === 'POST') body = { id: 'assignment-1', effective_from: '2026-07-20T00:00:00Z' }
    else if (path === '/api/v1/rates/preview-cost') body = { display_total: '0.25' }
    else if (path === '/api/v1/admin/rate-sources' && request.method() === 'GET') body = { configuration: rateConfiguration, last_successful_check: '2026-07-19T10:15:00Z', sources: rateSources }
    else if (path === '/api/v1/admin/rate-sources' && request.method() === 'POST') {
      const created = JSON.parse(request.postData() ?? '{}') as Record<string, unknown>
      const source = { ...created, id: 'source-2', enabled: true, last_success_at: undefined, consecutive_failures: 0 }
      rateSources = [...rateSources, source as typeof rateSources[number]]
      body = source
    }
    else if (path === '/api/v1/admin/rate-source-settings' && request.method() === 'PATCH') {
      const update = JSON.parse(request.postData() ?? '{}') as Partial<typeof rateConfiguration>
      rateConfiguration = { ...rateConfiguration, ...update, next_scheduled_run: '2026-07-26T10:15:00Z' }
      body = { updated: true, configuration: rateConfiguration }
    }
    else if (path === '/api/v1/admin/rate-candidates' && request.method() === 'GET') body = [{ id: 'candidate-1', status: candidateStatus, risk_level: 'manual_review', summary: { plan_code: 'TOU-D-4-9PM', material_differences: 1 }, created_at: '2026-07-20T10:20:00Z' }]
    else if (path === '/api/v1/admin/rate-candidates/candidate-1' && request.method() === 'GET') body = { id: 'candidate-1', status: candidateStatus, risk_level: 'manual_review', summary: { plan_code: 'TOU-D-4-9PM', material_differences: 1 }, created_at: '2026-07-20T10:20:00Z', source_evidence: { artifact_id: 'artifact-1', sha256: 'c'.repeat(64), captured_at: '2026-07-20T10:19:00Z', parser_id: 'sce_public_tou_html_v1', parser_version: '1.0.0', warnings: [] }, differences: [{ path: 'seasons.0.schedules.0.periods.0.price_per_kwh', change_type: 'changed', before: '0.34', after: '0.35', material: true }] }
    else if (path === '/api/v1/admin/rate-candidates/candidate-1/approve') { candidateStatus = 'approved'; body = { status: 'approved' } }
    else if (path === '/api/v1/admin/rate-candidates/candidate-1/activate') { candidateStatus = 'activated'; body = { status: 'active' } }
    else if (path === '/api/v1/admin/rate-sources/check-now') { body = { job_id: 'rate-job-1', status: 'queued' } }
    else if (path.match(/^\/api\/v1\/admin\/rate-sources\/[^/]+\/check$/) && request.method() === 'POST') { body = { job_id: 'rate-job-1', status: 'queued' } }
    else if (path === '/api/v1/jobs/rate-job-1') { jobPolls += 1; body = { id: 'rate-job-1', status: jobPolls > 1 ? 'succeeded' : 'running', progress: { completed: jobPolls > 1 ? 1 : 0, source_ids: ['source-1'] }, result: { candidate_count: 1 } } }
    else if (path === '/api/v1/admin/rate-checks') body = [{ id: 'check-1', rate_source_id: 'source-1', checked_at: '2026-07-20T10:20:00Z', outcome: 'succeeded', http_status: 200 }]
    else if (path === '/api/v1/alert-rules' && request.method() === 'GET') body = [
      { id: 'rule-disconnect', name: 'Sensor disconnected', rule_type: 'heartbeat_stale', severity: 'critical', enabled: true, debounce_seconds: 0, resolve_seconds: 30, configuration: { stale_seconds: 60 } },
      { id: 'rule-surge', name: 'Power surge', rule_type: 'power_surge', severity: 'critical', enabled: false, debounce_seconds: 10, resolve_seconds: 30, configuration: { threshold_watts: 5000 } },
    ]
    else if (path.startsWith('/api/v1/alert-rules/') && request.method() === 'PUT') body = { id: path.split('/').at(-1), enabled: true }
    else if (path === '/api/v1/notification-channels' && request.method() === 'GET') body = []
    else if (path === '/api/v1/notification-channels' && request.method() === 'POST') body = { id: 'smtp-1', name: 'Power Monitor email', channel_type: 'smtp', enabled: true, target: { host: 'smtp.example.com', port: 587, from: 'monitor@example.com', recipient_count: 1, starttls: true, implicit_tls: false, authentication_configured: true, event_types: ['heartbeat_stale', 'power_surge'] }, secrets_redacted: true }
    else if (path === '/api/v1/notification-attempts') body = []
    else if (path === '/api/v1/backups') body = []
    else if (path === '/api/v1/system/info') body = { product: 'Power Monitor Server', version: '1.0.0', protocol: 'pm-protocol/1.0.0', python_runtime: '3.13 production image', worker: { status: 'healthy', last_loop_at: '2026-07-20T19:05:00Z', last_success_at: '2026-07-20T19:05:00Z' }, defaults: { site: 'Upland Site', timezone: 'America/Los_Angeles', currency: 'USD', heartbeat_seconds: 15 } }
    else if (path === '/api/v1/admin/logs/availability') body = { earliest_date: '2026-06-01', latest_date: '2026-07-20', retention_days: 90, stored_size_bytes: 15360, last_rotation_at: '2026-07-20T02:00:00Z', services: [{ id: 'api', available: true, stored_size_bytes: 8192 }, { id: 'worker', available: true, stored_size_bytes: 4096 }, { id: 'enrollment', available: true, stored_size_bytes: 1024 }, { id: 'device_sync', available: true, stored_size_bytes: 1024 }, { id: 'rate_sync', available: false, stored_size_bytes: 0 }, { id: 'backup', available: true, stored_size_bytes: 1024 }] }
    else if (path === '/api/v1/admin/logs/exports' && request.method() === 'POST') {
      await new Promise((resolve) => setTimeout(resolve, 120))
      body = { id: 'log-export-1', status: 'ready', start_date: '2026-07-14', end_date: '2026-07-20', services: ['api', 'worker', 'enrollment', 'device_sync', 'rate_sync', 'backup'], size_bytes: 9000, download_url: '/api/v1/admin/logs/exports/log-export-1/download' }
    }
    else if (path === '/api/v1/admin/logs/exports/log-export-1/download') {
      await route.fulfill({ contentType: 'application/zip', body: 'mock-zip-content' })
      return
    }
    else if (path === '/api/v1/users') body = [
      { ...session(roles).user, is_active: true },
      { id: 'user-2', email: 'viewer@example.test', display_name: 'Dashboard Viewer', roles: ['viewer'], is_active: true },
    ]
    else if (path === '/api/v1/enrollment-tokens' && request.method() === 'POST') {
      enrollmentCounter += 1
      const payload = JSON.parse(request.postData() ?? '{}') as { name?: string }
      body = { id: `token-${enrollmentCounter}`, token: enrollmentCounter === 1 ? 'public-one-time-enrollment-token' : `public-one-time-enrollment-token-${enrollmentCounter}`, expires_at: new Date(Date.now() + 600_000).toISOString(), preassignment: { name: payload.name } }
    }
    else if (path === '/api/v1/alerts/alert-1/acknowledge') body = { acknowledged: true }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
  })
}

async function captureDashboardCorrection(page: Page, filename: string) {
  if (process.env.CAPTURE_DASHBOARD_SCREENSHOTS !== '1') return
  await page.screenshot({
    path: `../docs/screenshots/dashboard-corrections/${filename}`,
    fullPage: false,
  })
}

async function captureStatusLayout(page: Page, filename: string) {
  if (process.env.CAPTURE_STATUS_LAYOUT_SCREENSHOTS !== '1') return
  await page.evaluate(() => { window.scrollTo(0, 0) })
  await page.screenshot({
    path: `../docs/screenshots/status-indicators/${filename}`,
    fullPage: true,
  })
}

async function captureModernUi(page: Page, filename: string) {
  if (process.env.CAPTURE_MODERN_UI_SCREENSHOTS !== '1') return
  await page.evaluate(() => { window.scrollTo(0, 0) })
  await page.screenshot({
    path: `../docs/screenshots/modern-ui/${filename}`,
    fullPage: true,
  })
}

test('unauthenticated users see a secure sign-in surface', async ({ page }) => {
  await page.route('**/api/v1/auth/session', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ authenticated: false, bootstrap_required: false }) }))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Sign in to your dashboard' })).toBeVisible()
  await expect(page.getByText('Private fleet intelligence')).toBeVisible()
})

test('first run creates an administrator without a default password', async ({ page }) => {
  await page.route('**/api/v1/auth/session', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ authenticated: false, bootstrap_required: true }) }))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Create the administrator' })).toBeVisible()
  await expect(page.getByLabel('One-time bootstrap secret')).toBeVisible()
  await expect(page.getByText('There is no default password.')).toBeVisible()
})

test('Chrome-compatible native credential values submit by click and leave the login form', async ({ page }) => {
  await mockApplication(page, ['admin'], false)
  await page.goto('/sign-in')
  const form = page.locator('#login-form')
  const username = page.locator('#login-username')
  const password = page.locator('#current-password')
  await expect(form).toHaveAttribute('method', 'post')
  await expect(form).toHaveAttribute('autocomplete', 'on')
  await expect(username).toHaveAttribute('name', 'username')
  await expect(username).toHaveAttribute('type', 'email')
  await expect(username).toHaveAttribute('inputmode', 'email')
  await expect(username).toHaveAttribute('autocomplete', 'username')
  await expect(username).toHaveAttribute('autocapitalize', 'none')
  await expect(username).toHaveAttribute('spellcheck', 'false')
  await expect(password).toHaveAttribute('name', 'password')
  await expect(password).toHaveAttribute('type', 'password')
  await expect(password).toHaveAttribute('autocomplete', 'current-password')
  await expect(form.locator('input[name="username"]')).toHaveCount(1)
  await expect(form.locator('input[name="password"]')).toHaveCount(1)
  await expect(form.locator('input[type="hidden"]')).toHaveCount(0)

  const credentialValue = ['  Chrome Autofill', ' 42!  '].join('')
  await username.evaluate((input, value) => { (input as HTMLInputElement).value = value }, 'chrome.autofill@example.test')
  await password.evaluate((input, value) => { (input as HTMLInputElement).value = value }, credentialValue)
  const loginRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/auth/login') && request.method() === 'POST')
  await page.getByRole('button', { name: 'Sign in' }).click()
  const payload = JSON.parse((await loginRequest).postData() ?? '{}') as Record<string, unknown>
  expect(payload).toMatchObject({ email: 'chrome.autofill@example.test', password: credentialValue })
  await expect(form).toHaveCount(0)
  await expect(page).toHaveURL(/\/overview$/)
  await expect(page.getByRole('heading', { name: 'Power Dashboard' })).toBeVisible()
})

test('Enter submits login and password visibility keeps the same native input', async ({ page }) => {
  await mockApplication(page, ['admin'], false)
  await page.goto('/sign-in')
  const password = page.locator('#current-password')
  const originalNode = await password.evaluateHandle((node) => node)
  await page.locator('#login-username').fill('keyboard@example.test')
  await password.fill('Keyboard Password 42!')
  await password.evaluate((input) => { (input as HTMLInputElement).setSelectionRange(2, 8, 'forward') })
  await page.getByRole('button', { name: 'Show password' }).click()
  expect(await password.evaluate((node, original) => node === original, originalNode)).toBe(true)
  await expect(password).toHaveAttribute('type', 'text')
  await expect(password).toHaveAttribute('autocomplete', 'current-password')
  await expect(password).toHaveValue('Keyboard Password 42!')
  expect(await password.evaluate((input) => [(input as HTMLInputElement).selectionStart, (input as HTMLInputElement).selectionEnd])).toEqual([2, 8])
  await page.getByRole('button', { name: 'Hide password' }).click()
  await expect(password).toHaveAttribute('type', 'password')

  const loginRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/auth/login') && request.method() === 'POST')
  await password.press('Enter')
  await loginRequest
  await expect(page.locator('#login-form')).toHaveCount(0)
})

test('login focus, dark theme, mobile layout, and autofill rules remain accessible', async ({ page }) => {
  await mockApplication(page, ['admin'], false)
  await page.setViewportSize({ width: 375, height: 760 })
  await page.goto('/sign-in')
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark' })
  const username = page.locator('#login-username')
  await username.focus()
  await expect(username).toBeFocused()
  const styles = await username.evaluate((input) => {
    const computed = getComputedStyle(input)
    return { color: computed.color, backgroundColor: computed.backgroundColor, caretColor: computed.caretColor, outlineStyle: computed.outlineStyle }
  })
  expect(styles.color).not.toBe(styles.backgroundColor)
  expect(styles.caretColor).not.toBe('transparent')
  expect(styles.outlineStyle).not.toBe('none')
  expect(await page.evaluate(() => Array.from(document.styleSheets).some((sheet) => Array.from(sheet.cssRules).some((rule) => rule.cssText.includes(':-webkit-autofill'))))).toBe(true)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()
})

test('viewer sees fleet evidence and an in-app denial for restricted pages', async ({ page }) => {
  await mockApplication(page, ['viewer'])
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Power Dashboard' })).toBeVisible()
  await expect(page.getByText('960 W', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('link', { name: /Enroll sensor/ })).toHaveCount(0)
  await page.goto('/enrollment')
  await expect(page.getByRole('alert')).toContainText('This page is restricted')
  await page.goto('/administration/access')
  await expect(page.getByRole('alert')).toContainText('This page is restricted')
})

test('history combines sensors, shows TOU cost provenance, selects a range, and exports CSV', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/analytics/history')
  await expect(page.getByRole('heading', { name: 'Analytics' })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'History' })).toHaveAttribute('aria-selected', 'true')
  await page.getByRole('combobox', { name: 'Scope', exact: true }).selectOption('devices')
  await page.getByRole('button', { name: 'Select all eligible sensors' }).click()
  await expect(page.getByText('2 selected')).toBeVisible()
  await page.getByLabel('Display mode').selectOption('combined_plus_individual')
  await page.getByLabel('Metric').selectOption('usage_cost')
  await expect(page.getByText(/Combined total · 2 sensors/)).toBeVisible()
  await expect(page.getByText('$4.00', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Deterministic TOU plan').first()).toBeVisible()
  await expect(page.getByText('partial_coverage').first()).toBeVisible()
  await page.getByLabel(/Include .*8:00 PM.*selected range/).first().check()
  await expect(page.getByRole('heading', { name: 'Selected range summary' })).toBeVisible()
  await expect(page.getByText(/server-calculated interval segments/i)).toBeVisible()
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export CSV' }).click()
  await download
  await expect(page.getByText('History export downloaded.')).toBeVisible()

  if (process.env.CAPTURE_HISTORY_SCREENSHOT === '1') {
    await page.screenshot({ path: '../docs/screenshots/history-cost-aggregation.png', fullPage: true })
  }

  await page.setViewportSize({ width: 375, height: 760 })
  await expect(page.getByRole('combobox', { name: 'Scope', exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
})

test('topology shows an explicitly confirmed overlap warning', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/monitoring/topology')
  await expect(page.getByRole('heading', { name: 'Monitoring' })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Topology' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByText('Potential overlap was explicitly confirmed.')).toBeVisible()
  await expect(page.getByText('Never summed by default')).toBeVisible()
})

test('cost workspace is available from navigation and routing', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/analytics/costs')
  await expect(page).toHaveURL(/\/analytics\/costs$/)
  await expect(page.getByRole('heading', { name: 'Analytics', exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Costs' })).toHaveAttribute('aria-selected', 'true')
})

test('admin can create an enrollment token without seeing a permanent secret', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/enrollment')
  await page.getByLabel('Friendly name').fill('Garage HVAC')
  await page.getByRole('button', { name: /Add enrollment token/ }).click()
  await expect(page.getByText('1 token ready')).toBeVisible()
  await expect(page.getByText('public-one-time-enrollment-token')).toBeVisible()
  await page.getByLabel('Friendly name').fill('Water Heater')
  await page.getByRole('button', { name: /Add enrollment token/ }).click()
  await expect(page.getByText('2 tokens ready')).toBeVisible()
  await expect(page.getByText('Water Heater')).toBeVisible()
  await expect(page.getByText('Permanent device secrets are never shown here.')).toBeVisible()
})

test('Users & Access is the canonical user creation and removal workflow', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/administration/users-access')
  await page.getByRole('button', { name: /Add user/ }).click()
  await page.getByLabel('Display name').fill('Energy Analyst')
  await page.getByLabel('Email address').fill('analyst@example.test')
  await page.getByLabel('Temporary password').fill('Production-Password-42!')
  const createRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/users') && request.method() === 'POST')
  await page.getByRole('button', { name: /Create user/ }).click()
  await createRequest

  const viewerRow = page.getByRole('row').filter({ hasText: 'Dashboard Viewer' })
  await viewerRow.getByRole('button', { name: 'View access' }).click()
  const accessDialog = page.getByRole('dialog', { name: 'User access details' })
  await accessDialog.getByRole('button', { name: 'Remove user' }).click()
  const removalDialog = page.getByRole('dialog', { name: 'remove user' })
  await removalDialog.getByLabel(/Reason/).fill('Contract ended')
  const removeUserButton = removalDialog.getByRole('button', { name: 'Remove user' })
  await expect(removeUserButton).toBeDisabled()
  await removalDialog.getByLabel(/exact email address/).fill('not-the-user@example.test')
  await expect(removeUserButton).toBeDisabled()
  await removalDialog.getByLabel(/exact email address/).fill('viewer@example.test')
  await removalDialog.getByLabel('Current password').fill('Production-Password-42!')
  const removeRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/admin/users/user-2/remove') && request.method() === 'POST')
  await removeUserButton.click()
  await removeRequest
  await expect(page.getByText(/User removed.*history was preserved/)).toBeVisible()

  await page.getByRole('combobox', { name: 'Status', exact: true }).selectOption('removed')
  await expect(page.getByRole('row').filter({ hasText: 'Dashboard Viewer' })).toContainText('Removed')
  await accessDialog.getByRole('button', { name: 'Restore' }).click()
  const restoreDialog = page.getByRole('dialog', { name: 'restore user' })
  await restoreDialog.getByLabel(/Reason/).fill('Returning contractor')
  const restoreRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/admin/users/user-2/restore') && request.method() === 'POST')
  await restoreDialog.getByRole('button', { name: 'Restore user' }).click()
  await restoreRequest
  await expect(page.getByText(/restored in a disabled, unassigned state/i)).toBeVisible()
})

test('legacy user-management routes redirect to Users & Access', async ({ page }) => {
  await mockApplication(page)
  for (const route of ['/admin?tab=users', '/administration/users', '/administration/users-roles', '/administration/roles']) {
    await page.goto(route)
    await expect(page).toHaveURL(/\/administration\/access$/)
    await expect(page.getByRole('heading', { name: 'Administration' })).toBeVisible()
  }
  await page.goto('/admin?tab=users&search=viewer&status=disabled&unsafe=discarded')
  await expect(page).toHaveURL('/administration/access?search=viewer&status=disabled')
})

test('administrator previews effective access and promotes a site-scoped user', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/administration/users-access')
  await expect(page.getByRole('heading', { name: 'Administration' })).toBeVisible()
  await page.getByLabel('Search users').fill('Dashboard Viewer')
  const row = page.getByRole('row').filter({ hasText: 'Dashboard Viewer' })
  await expect(row).toContainText('3 effective')
  await row.getByRole('button', { name: 'View access' }).click()
  const dialog = page.getByRole('dialog', { name: 'User access details' })
  await expect(dialog.getByText('192.168.0.20')).toBeVisible()
  await expect(dialog.getByText('Operational assignment')).toBeVisible()
  await dialog.getByRole('button', { name: /Edit access/ }).click()
  await dialog.getByRole('checkbox', { name: /Operator/ }).check()
  await expect(dialog.getByText('Manage devices')).toBeVisible()
  await expect(dialog.getByText(/2 active session\(s\) will be revoked/)).toBeVisible()
  await dialog.getByRole('checkbox', { name: /I reviewed the privilege increase/ }).check()
  await dialog.getByLabel('Current password').fill('Production-Password-42!')
  const updateRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/admin/users/user-2/access') && request.method() === 'PUT')
  await dialog.getByRole('button', { name: 'Save access' }).click()
  const payload = JSON.parse((await updateRequest).postData() ?? '{}') as { role_ids: string[]; site_ids: string[]; confirm_high_risk: boolean }
  expect(payload.role_ids).toEqual(expect.arrayContaining(['viewer', 'operator']))
  expect(payload.site_ids).toEqual(['site-1'])
  expect(payload.confirm_high_risk).toBe(true)
  await expect(page.getByText(/User access updated\. 2 session/)).toBeVisible()
})

test('administrator drafts, previews, and publishes safe login text', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/administration/interface-text')
  await expect(page.getByRole('heading', { name: 'Administration' })).toBeVisible()
  await page.getByRole('tab', { name: 'Login Screen' }).click()
  await page.getByLabel('Login heading').fill('Welcome to the Upland energy dashboard')
  const draftRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/admin/interface-text/draft') && request.method() === 'PUT')
  await page.getByRole('button', { name: 'Save draft' }).first().click()
  await draftRequest
  await expect(page.getByText('Text draft saved.')).toBeVisible()
  await page.getByRole('button', { name: 'Preview' }).click()
  await expect(page.getByText('Welcome to the Upland energy dashboard').first()).toBeVisible()
  await expect(page.getByText(/Nothing has been published/)).toBeVisible()
  await page.getByRole('button', { name: 'Publish', exact: true }).first().click()
  const publishDialog = page.getByRole('dialog', { name: 'Publish interface text' })
  await expect(publishDialog.getByText(/Internal routes, API identifiers, and permission codes will not change/)).toBeVisible()
  await publishDialog.getByRole('button', { name: 'Confirm and publish' }).click()
  await expect(page.getByText('Interface text published.')).toBeVisible()
})

test('administrator removes a claimed sensor with exact confirmation and can view it archived', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/enrollment')
  const claimedRow = page.getByRole('row').filter({ hasText: 'Garage HVAC' })
  await claimedRow.getByRole('button', { name: 'Remove sensor' }).click()
  const dialog = page.getByRole('dialog', { name: 'Remove sensor' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('device-1')).toBeVisible()
  await expect(dialog.getByText('Upland Site · Garage branch')).toBeVisible()
  await expect(dialog.getByText('1,440')).toBeVisible()
  await captureDashboardCorrection(page, 'sensor-removal-confirmation.png')
  const confirmation = dialog.getByLabel(/Type Garage HVAC or the immutable ID to confirm/)
  const removeButton = dialog.getByRole('button', { name: 'Remove sensor', exact: true })
  await expect(removeButton).toBeDisabled()
  await confirmation.fill('wrong value')
  await expect(dialog.getByText('The confirmation does not match')).toBeVisible()
  await confirmation.fill('Garage HVAC')
  await dialog.getByLabel(/Removal reason/).selectOption('replaced')
  const removalRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/admin/devices/device-1/unclaim') && request.method() === 'POST')
  await removeButton.click()
  const payload = JSON.parse((await removalRequest).postData() ?? '{}') as { confirmation: string; reason: string }
  expect(payload).toEqual({ confirmation: 'Garage HVAC', reason: 'replaced' })
  await expect(page.getByText('Sensor removed successfully.')).toBeVisible()
  await expect(page.getByText('No claimed sensors')).toBeVisible()
  await page.getByRole('tab', { name: /Archived sensors/ }).click()
  await expect(page.getByRole('row').filter({ hasText: 'Garage HVAC' })).toContainText('Preserved')
  await expect(page.getByText('Re-enrollment allowed')).toBeVisible()
  await captureDashboardCorrection(page, 'archived-sensors.png')
})

test('administrator can configure SMTP and notification timing', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/administration/notifications')
  await expect(page.getByRole('heading', { name: 'Administration' })).toBeVisible()
  await page.getByRole('button', { name: 'Configure SMTP' }).click()
  await page.getByLabel('SMTP host').fill('smtp.example.com')
  await page.getByLabel('Username').fill('mailer')
  await page.getByLabel('Password').fill('smtp-secret-password')
  await page.getByLabel('From address').fill('monitor@example.com')
  await page.getByLabel('Recipients').fill('owner@example.com')
  const smtpRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/notification-channels') && request.method() === 'POST')
  await page.getByRole('button', { name: 'Save SMTP securely' }).click()
  const smtpPayload = JSON.parse((await smtpRequest).postData() ?? '{}') as { configuration: { event_types: string[] } }
  expect(smtpPayload.configuration.event_types).toEqual(expect.arrayContaining(['heartbeat_stale', 'power_surge', 'rate_candidate_pending', 'rate_source_conflict']))

  await page.getByLabel('Enable power surge notifications').check()
  await page.getByLabel('Power surge threshold').fill('7200')
  await page.getByLabel('Power surge duration').fill('15')
  const surgeRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/alert-rules/rule-surge') && request.method() === 'PUT')
  await page.getByRole('button', { name: 'Save notification triggers' }).click()
  const surgePayload = JSON.parse((await surgeRequest).postData() ?? '{}') as { enabled: boolean; debounce_seconds: number; configuration: { threshold_watts: number } }
  expect(surgePayload).toMatchObject({ enabled: true, debounce_seconds: 15, configuration: { threshold_watts: 7200 } })
})

test('rate manager creates, validates, activates, and assigns a custom TOU plan', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/billing/rate-plans')
  await expect(page.getByRole('heading', { name: 'Billing' })).toBeVisible()
  await page.getByRole('button', { name: /Create custom plan/i }).click()
  await page.getByLabel('Plan name').fill('Weekday and weekend test plan')
  await page.getByLabel('Plan code').fill('CUSTOM-E2E')
  await page.getByRole('button', { name: /Next/ }).click()
  await expect(page.getByRole('button', { name: /Pricing & tiers/ })).toHaveAttribute('aria-current', 'step')
  await expect(page.getByLabel('Pricing model')).toHaveValue('time_of_use')
  await page.getByRole('button', { name: /Next/ }).click()
  await expect(page.getByRole('button', { name: /TOU schedules/ })).toHaveAttribute('aria-current', 'step')
  await expect(page.getByLabel('Total price per kWh')).toHaveValue('0.25000000')
  await page.getByLabel('End minute').fill('1380')
  await expect(page.getByText('This schedule does not cover the full day')).toBeVisible()
  await page.getByLabel('End minute').fill('1440')
  await expect(page.getByText('This schedule does not cover the full day')).toHaveCount(0)
  const chargesStep = page.getByRole('button', { name: /Charges & adjustments/ })
  await chargesStep.focus()
  await page.keyboard.press('Enter')
  await expect(chargesStep).toHaveAttribute('aria-current', 'step')
  await expect(page.getByText('Whole-account items are ignored')).toBeVisible()
  await page.getByRole('button', { name: /Next/ }).click()
  await page.getByRole('button', { name: /Save draft/ }).click()
  await expect(page).toHaveURL(/\/billing\/rate-plans\/custom-plan-1\/versions\/custom-version-1/)
  await page.getByRole('button', { name: 'Validate', exact: true }).click()
  await expect(page.getByText('Ready to activate')).toBeVisible()
  await page.getByRole('button', { name: 'Activate', exact: true }).click()
  const activationDialog = page.getByRole('dialog', { name: 'Activate rate version' })
  await expect(activationDialog).toBeVisible()
  await activationDialog.getByRole('button', { name: 'Activate version' }).click()
  await expect(page.getByLabel('Utility account')).toBeEnabled()
  await page.getByLabel('Utility account').selectOption('account-1')
  await expect(page.getByText('Rate version assigned.')).toBeVisible()
})

test('administrator monitors an SCE job and reviews candidate evidence', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/billing/rate-sources')
  await page.getByRole('button', { name: /Check SCE now/ }).click()
  await expect(page.getByText('SCE check succeeded')).toBeVisible({ timeout: 8_000 })
  await page.getByRole('button', { name: /TOU-D-4-9PM/ }).click()
  await expect(page.getByText('Archived evidence')).toBeVisible()
  await expect(page.getByText(/price_per_kwh/)).toBeVisible()
  await page.getByRole('button', { name: 'Approve' }).click()
  await expect(page.getByRole('button', { name: /Activate approved version/ })).toBeVisible()
  await page.getByRole('button', { name: /Activate approved version/ }).click()
  await expect(page.getByText('activated').last()).toBeVisible()
})

test('rate source settings save, confirm, and survive a fresh reload', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/billing/rate-sources')
  await page.getByLabel('Activation policy').selectOption('auto_activate_verified')
  await page.getByLabel('Enable strict automatic activation').check()
  const updateRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/admin/rate-source-settings') && request.method() === 'PATCH')
  await page.getByRole('button', { name: 'Save settings' }).click()
  const payload = JSON.parse((await updateRequest).postData() ?? '{}') as Record<string, unknown>
  expect(payload).toMatchObject({ approval_mode: 'auto_activate_verified', auto_activate_verified: true })
  expect(payload).not.toHaveProperty('next_scheduled_run')
  await expect(page.getByText('Rate source settings saved.')).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('Activation policy')).toHaveValue('auto_activate_verified')
  await expect(page.getByLabel('Enable strict automatic activation')).toBeChecked()
})

test('administrator adds an approved SCE source and can queue its first scrape', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/billing/rate-sources')
  await page.getByRole('button', { name: 'Add source' }).click()
  await page.getByLabel('Source name').fill('SCE comparison page')
  await page.getByLabel('Official SCE HTTPS URL').fill('https://www.sce.com/save-money/rates-financing/rate-plan-comparison')
  await page.getByLabel(/Effective date/).fill('2026-06-01')
  const createRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/admin/rate-sources') && request.method() === 'POST')
  await page.getByRole('button', { name: 'Add approved source' }).click()
  const payload = JSON.parse((await createRequest).postData() ?? '{}') as Record<string, unknown>
  expect(payload).toMatchObject({ parser_id: 'sce_public_tou_html_v1', effective_from: '2026-06-01' })
  await expect(page.getByText('Source added. Run its check to create review candidates.')).toBeVisible()
  const sourceRow = page.locator('.source-list article').filter({ hasText: 'SCE comparison page' })
  await expect(sourceRow).toBeVisible()
  await sourceRow.getByRole('button', { name: 'Check' }).click()
  await expect(page.getByText('SCE check succeeded')).toBeVisible({ timeout: 8_000 })
  await page.reload()
  await expect(page.getByText('SCE comparison page')).toBeVisible()
})

test('six-workspace shell and complete site lifecycle stay canonical, audited, and responsive', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await mockApplication(page)
  await page.goto('/administration/sites-network')
  await expect(page.locator('.sidebar nav a')).toHaveCount(6)
  await expect(page.locator('.sidebar nav a')).toHaveText([
    'Overview',
    'Monitoring',
    'Analytics',
    'Billing',
    'Alerts',
    'Administration',
  ])
  await expect(page.getByRole('button', { name: 'Physical Sites' })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('button', { name: 'Add site' })).toHaveCount(1)
  await captureModernUi(page, 'sites-desktop-wide-before.png')

  await page.getByRole('button', { name: 'Add site' }).click()
  const wizard = page.getByRole('dialog', { name: 'Add physical site' })
  await wizard.getByLabel('Site display name').fill('Workshop Site')
  await expect(wizard.getByLabel('Stable site code')).toHaveValue('workshop-site')
  await wizard.getByLabel(/Description/).fill('Detached workshop energy boundary.')
  await wizard.getByRole('button', { name: /Continue/ }).click()
  await wizard.getByLabel('IANA timezone').fill('America/Denver')
  await wizard.getByRole('button', { name: /Continue/ }).click()
  await wizard.getByRole('radio', { name: /explicit private-network policy/ }).check()
  await wizard.getByRole('button', { name: /Continue/ }).click()
  await wizard.getByRole('button', { name: /Continue/ }).click()
  await wizard.getByRole('radio', { name: /Configure later/ }).check()
  await wizard.getByRole('button', { name: /Continue/ }).click()
  await wizard.getByRole('checkbox', { name: /reviewed the site identity/ }).check()
  await wizard.getByRole('button', { name: 'Create site' }).click()
  await expect(page.getByRole('status')).toContainText('Site created.')

  let details = page.getByRole('dialog', { name: 'Physical site details' })
  await expect(details.getByRole('heading', { name: 'Workshop Site' })).toBeVisible()
  await captureModernUi(page, 'sites-desktop-wide-after-create.png')
  await details.getByRole('button', { name: 'Set as default' }).click()
  await expect(page.getByRole('status')).toContainText('Default site updated.')
  await expect(details.getByText('Default site')).toBeVisible()
  await details.getByRole('button', { name: 'Edit' }).click()
  await details.getByLabel('Display name').fill('Workshop Energy Site')
  await details.getByLabel('Change reason').fill('Use the approved facility name')
  await details.getByRole('button', { name: 'Save site' }).click()
  await expect(page.getByRole('status')).toContainText('Site details updated.')
  await expect(details.getByRole('heading', { name: 'Workshop Energy Site' })).toBeVisible()

  await details.getByRole('button', { name: 'Disable' }).click()
  let lifecycle = page.getByRole('dialog', { name: 'disable site' })
  await lifecycle.getByLabel('Reason').fill('Temporary maintenance')
  await lifecycle.getByRole('button', { name: 'Disable site', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('Site disabled.')
  await details.getByRole('button', { name: 'Enable' }).click()
  lifecycle = page.getByRole('dialog', { name: 'enable site' })
  await lifecycle.getByLabel('Reason').fill('Maintenance complete')
  await lifecycle.getByRole('button', { name: 'Enable site', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('Site enabled.')
  await details.getByRole('tab', { name: 'Audit history' }).click()
  await expect(details.getByText('site.updated')).toBeVisible()
  await details.getByRole('button', { name: 'Close site details' }).click()

  const uplandRow = page.locator('.site-row').filter({ hasText: 'Upland Site' })
  await uplandRow.getByRole('button', { name: 'View details' }).click()
  details = page.getByRole('dialog', { name: 'Physical site details' })
  await details.getByRole('button', { name: 'Set as default' }).click()
  await expect(details.getByText('Default site')).toBeVisible()
  await details.getByRole('button', { name: 'Close site details' }).click()

  const garageRow = page.locator('.site-row').filter({ hasText: 'Garage Site' })
  await garageRow.getByRole('button', { name: 'View details' }).click()
  details = page.getByRole('dialog', { name: 'Physical site details' })
  await details.getByRole('button', { name: 'Remove site' }).click()
  lifecycle = page.getByRole('dialog', { name: 'remove site' })
  await expect(lifecycle.getByText('Historical records are retained')).toBeVisible()
  await lifecycle.getByLabel('Sensors action for Garage meter').selectOption('archive')
  await lifecycle.getByLabel('Utility accounts action for Garage electric').selectOption('archive')
  await lifecycle.getByRole('checkbox', { name: /Dashboard Viewer/ }).check()
  await lifecycle.getByLabel('Removal reason').fill('Garage merged into the primary boundary')
  await lifecycle.getByRole('button', { name: 'Resolve selected dependencies' }).click()
  await expect(page.getByRole('status')).toContainText('Site dependencies resolved.')
  await lifecycle.getByLabel('Type exact site name or code').fill('Garage Site')
  await lifecycle.getByRole('checkbox', { name: /reviewed retained history/ }).check()
  await lifecycle.getByRole('button', { name: 'Remove site', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('Site removed from active navigation.')
  await expect(page.getByLabel('Current site').locator('option')).not.toContainText(['Garage Site'])
  await expect(details).toHaveCount(0)

  await page.locator('.site-list-controls select').first().selectOption('removed')
  details = page.getByRole('dialog', { name: 'Physical site details' })
  await expect(details.getByRole('heading', { name: 'Garage Site' })).toBeVisible()
  await details.getByRole('button', { name: 'Restore site' }).click()
  lifecycle = page.getByRole('dialog', { name: 'restore site' })
  await lifecycle.getByLabel('Reason').fill('Restoring for administrator review')
  await lifecycle.getByRole('checkbox', { name: /review users, network policy/ }).check()
  await lifecycle.getByRole('button', { name: 'Restore site', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('Site restored in a disabled state.')
  await expect(page.getByLabel('Current site').locator('option')).not.toContainText(['Garage Site'])
  await expect(details).toHaveCount(0)

  const duplicateActions = await page.locator('.action-scope').first().evaluate((root) => {
    const counts = new Map<string, number>()
    root.querySelectorAll<HTMLElement>('[data-action-id]:not([hidden])').forEach((element) => {
      const action = element.dataset.actionId ?? ''
      const resource = element.dataset.actionResource ?? ''
      const identity = resource ? `${action}:${resource}` : action
      counts.set(identity, (counts.get(identity) ?? 0) + 1)
    })
    return [...counts.entries()].filter(([, count]) => count > 1)
  })
  expect(duplicateActions).toEqual([])

  for (const viewport of [
    { width: 1024, height: 900, name: 'sites-desktop-compact.png' },
    { width: 768, height: 1024, name: 'sites-tablet.png' },
    { width: 390, height: 844, name: 'sites-mobile.png' },
  ]) {
    await page.setViewportSize(viewport)
    await page.goto('/administration/sites-network')
    await expect(page.getByRole('button', { name: 'Physical Sites' })).toHaveAttribute('aria-current', 'page')
    await expect(page.getByRole('button', { name: 'Add site' })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
    await captureModernUi(page, viewport.name)
  }
})

test('administration workspaces and status pills stay inside their containers', async ({ page }) => {
  await page.setViewportSize({ width: 1512, height: 768 })
  await mockApplication(page)
  await page.goto('/administration/sites-network')
  await expect(page.getByRole('heading', { name: 'Administration' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Physical Sites' })).toHaveAttribute('aria-current', 'page')
  await expect(page.locator('.admin-tabs')).toHaveCount(0)

  await page.goto('/administration/security?view=health')
  const diagnostic = page.locator('[data-status-zone="administration_diagnostics"]')
  const diagnosticCard = diagnostic.locator('.status-indicator').first()
  const [diagnosticBox, diagnosticCardBox, diagnosticStatusBox] = await Promise.all([
    diagnostic.boundingBox(),
    diagnosticCard.boundingBox(),
    diagnosticCard.locator('.status-severity-text').boundingBox(),
  ])
  expect(diagnosticBox).not.toBeNull()
  expect(diagnosticCardBox).not.toBeNull()
  expect(diagnosticStatusBox).not.toBeNull()
  expect((diagnosticCardBox?.x ?? 0) + (diagnosticCardBox?.width ?? 0)).toBeLessThanOrEqual((diagnosticBox?.x ?? 0) + (diagnosticBox?.width ?? 0))
  expect((diagnosticStatusBox?.x ?? 0) + (diagnosticStatusBox?.width ?? 0)).toBeLessThanOrEqual((diagnosticCardBox?.x ?? 0) + (diagnosticCardBox?.width ?? 0))
  const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)
  expect(horizontalOverflow).toBe(false)
})

test('administrator configures an effective utility account and explicit private sensor network', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/billing/accounts?rate_version_id=rate-version-official')
  await expect(page.getByText('No utility account configured')).toBeVisible()
  await page.getByRole('button', { name: 'Create utility account' }).first().click()
  await page.getByLabel('Account display name').fill('Upland main electric')
  await page.getByRole('button', { name: /Next/ }).click()
  await expect(page.getByRole('dialog', { name: 'Utility & provider' })).toBeVisible()
  await page.getByRole('button', { name: /Next/ }).click()
  await expect(page.getByRole('dialog', { name: 'Billing details' })).toBeVisible()
  await page.getByLabel('Billing-cycle start day').fill('17')
  await page.getByRole('button', { name: /Next/ }).click()
  await expect(page.getByRole('dialog', { name: 'Rate & version' })).toBeVisible()
  await expect(page.getByLabel('Published rate plan and version')).toHaveValue('rate-version-official')
  await expect(page.getByText('On Peak $0.58/kWh')).toBeVisible()
  await page.getByRole('button', { name: /Next/ }).click()
  await expect(page.getByRole('radio', { name: /Energy-only monitored scope/ })).toBeChecked()
  await page.getByRole('button', { name: /Next/ }).click()
  await expect(page.getByRole('dialog', { name: 'Adjustments' })).toBeVisible()
  await page.getByRole('button', { name: /Next/ }).click()
  await expect(page.getByRole('dialog', { name: 'Review & create' })).toBeVisible()
  await page.getByRole('button', { name: 'Confirm and create' }).click()
  await expect(page.getByText('Utility account created and rate assignment activated.')).toBeVisible()
  const account = page.locator('.utility-account-card').filter({ hasText: 'Upland main electric' })
  await expect(account).toContainText('TOU-D 4 PM to 9 PM')
  await expect(account).toContainText('On Peak')
  await expect(account).toContainText('$0.58/kWh')
  await expect(account).toContainText('Energy-only monitored scope')
  if (process.env.CAPTURE_UTILITY_NETWORK_SCREENSHOTS === '1') {
    await page.screenshot({ path: '../docs/screenshots/utility-account-management.png', fullPage: true })
  }

  await page.goto('/administration/sites-network?view=network')
  await expect(page.getByRole('heading', { name: 'Sensor network policy' })).toBeVisible()
  await page.getByRole('tab', { name: 'Server pull access' }).click()
  await expect(page.getByText('Device network access denied')).toBeVisible()
  await page.getByLabel('IPv4 or IPv6 CIDR').fill('192.168.50.99/24')
  await page.getByRole('button', { name: 'Add CIDR' }).click()
  await expect(page.getByText('CIDR added.')).toBeVisible()
  await expect(page.getByText('192.168.50.0/24')).toBeVisible()
  await page.getByRole('radio', { name: /Allow listed private networks only/ }).check()
  await page.getByRole('button', { name: 'Save policy' }).click()
  await expect(page.getByText('Network policy updated.')).toBeVisible()

  const address = page.getByLabel('Address')
  await address.fill('192.168.50.42')
  await page.getByRole('button', { name: 'Test address' }).click()
  await expect(page.getByText('Allowed', { exact: true })).toBeVisible()
  await expect(page.getByText('Sensor VLAN (192.168.50.0/24)')).toBeVisible()
  await address.fill('192.168.60.42')
  await page.getByRole('button', { name: 'Test address' }).click()
  await expect(page.getByText('Blocked', { exact: true })).toBeVisible()
  if (process.env.CAPTURE_UTILITY_NETWORK_SCREENSHOTS === '1') {
    await page.evaluate(() => { window.scrollTo(0, 0) })
    await page.screenshot({ path: '../docs/screenshots/sensor-network-policy.png', fullPage: true })
  }

  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
})

test('disabled controls in rate details use a disabled cursor, not a busy spinner', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/billing/rate-plans')
  await page.getByRole('button', { name: 'View details' }).click()
  const previous = page.getByRole('button', { name: 'Previous' })
  await expect(previous).toBeDisabled()
  await expect(previous).toHaveCSS('cursor', 'not-allowed')
  const disabledCursors = await page.locator('.button:disabled').evaluateAll((buttons) =>
    buttons.map((button) => getComputedStyle(button).cursor),
  )
  expect(disabledCursors).not.toContain('wait')
})

test('administrator downloads a seven-day application-log export from Data Management', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/administration/data')
  await expect(page.getByRole('heading', { name: 'Application logs' })).toBeVisible()
  await expect(page.getByText('90 days', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Start date')).toHaveValue('2026-07-14')
  await expect(page.getByLabel('End date')).toHaveValue('2026-07-20')
  await expect(page.getByLabel('Service or category')).toHaveValue('all')
  await captureDashboardCorrection(page, 'application-logs.png')
  const exportRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/admin/logs/exports') && request.method() === 'POST')
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download logs' }).click()
  await expect(page.getByText('Preparing and securely redacting the export…')).toBeVisible()
  const exportPayload = JSON.parse((await exportRequest).postData() ?? '{}') as { services: string[] }
  expect(exportPayload.services).toHaveLength(6)
  await download
  await expect(page.getByText('Log export is ready.')).toBeVisible()
})

test('dashboard copy is corrected without exposing protocol or footer status text', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/')
  await expect(page.getByText('Fleet availability')).toHaveCount(0)
  await expect(page.getByText('100%', { exact: true })).toBeVisible()
  await expect(page.locator('[data-indicator-key="rate.current_plan"]')).toHaveCount(0)
  await expect(page.locator('[data-indicator-key="rate.current_period"]')).toHaveCount(0)
  await page.goto('/billing/rate-plans')
  await expect(page.locator('[data-indicator-key="rate.current_plan"]')).toContainText('Upland SCE account')
  await expect(page.locator('[data-indicator-key="rate.current_period"]')).toContainText('On-peak')
  await expect(page.locator('[data-indicator-key="rate.current_price"]')).toContainText('$0.58/kWh')
  await page.goto('/monitoring/devices')
  await expect(page.getByRole('heading', { name: 'Monitoring', exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Devices' })).toHaveAttribute('aria-selected', 'true')
  await captureDashboardCorrection(page, 'device-management.png')
  await expect(page.getByText(/pm-protocol\/1\.0\.0/i)).toHaveCount(0)
  await expect(page.getByText(/server protected/i)).toHaveCount(0)
  await expect(page.getByText(/multi-sensor fleet/i)).toHaveCount(0)
  await expect(page.getByText(/signed heartbeats.*local custody/i)).toHaveCount(0)
  await page.goto('/alerts')
  await expect(page.getByRole('heading', { name: 'Alerts', exact: true })).toBeVisible()
  await expect(page).toHaveTitle('Alerts · Power Monitor')
  await expect(page.getByText(/Evidence, debounce, resolution, acknowledgement/)).toHaveCount(0)
  await captureDashboardCorrection(page, 'alerts-and-notifications.png')
})

test('search and dropdown controls keep compact pointer focus and visible keyboard focus', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/monitoring/devices')
  const search = page.getByPlaceholder('Search devices')
  const initialBox = await search.boundingBox()
  await search.click()
  expect(await search.evaluate((element) => getComputedStyle(element).outlineWidth)).toBe('0px')
  expect(await search.evaluate((element) => getComputedStyle(element).boxShadow)).toBe('none')
  expect(await search.evaluate((element) => element.parentElement ? getComputedStyle(element.parentElement).boxShadow : '')).not.toContain('3px')
  expect(await search.boundingBox()).toEqual(initialBox)

  await page.locator('body').click({ position: { x: 2, y: 2 } })
  for (let index = 0; index < 30; index += 1) {
    await page.keyboard.press('Tab')
    if (await search.evaluate((element) => element === document.activeElement)) break
  }
  expect(await search.evaluate((element) => element.matches(':focus-visible'))).toBe(true)
  expect(await search.evaluate((element) => element.parentElement ? getComputedStyle(element.parentElement).outlineWidth : '')).toBe('2px')
  await page.keyboard.type('Garage')
  await expect(search).toHaveValue('Garage')

  const status = page.getByRole('combobox', { name: /^Status/ })
  await status.focus()
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Enter')
  await expect(status).not.toHaveValue('all')
  expect(await status.evaluate((element) => element.parentElement ? getComputedStyle(element.parentElement).outlineWidth : '')).toBe('2px')

  await page.locator('body').click({ position: { x: 2, y: 2 } })
  const restingStyles = await status.evaluate((element) => {
    const styles = getComputedStyle(element.parentElement as HTMLElement)
    return { borderColor: styles.borderColor, boxShadow: styles.boxShadow, outlineStyle: styles.outlineStyle }
  })
  await status.click()
  await status.selectOption('offline_last_known')
  await expect(status).not.toBeFocused()
  expect(await status.evaluate((element) => {
    const styles = getComputedStyle(element.parentElement as HTMLElement)
    return { borderColor: styles.borderColor, boxShadow: styles.boxShadow, outlineStyle: styles.outlineStyle }
  })).toEqual(restingStyles)
})

test('alert acknowledgement calls the audited server action', async ({ page }) => {
  await mockApplication(page)
  const acknowledgement = page.waitForRequest((request) => request.url().endsWith('/api/v1/alerts/alert-1/acknowledge') && request.method() === 'POST')
  await page.goto('/alerts')
  await page.getByRole('button', { name: 'Acknowledge' }).click()
  await acknowledgement
})

test('mobile navigation opens with keyboard-operable controls', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 760 })
  await mockApplication(page)
  await page.goto('/')
  const menu = page.getByRole('button', { name: 'Open navigation' })
  await menu.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible()
  const drawerBox = await page.locator('.sidebar-open').boundingBox()
  expect(drawerBox?.width).toBeGreaterThanOrEqual(250)
  await expect(page.locator('.sidebar-open')).toHaveCSS('transform', 'none')
  await expect(page.getByRole('link', { name: 'Monitoring' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Current site', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Manage sites' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
  await captureModernUi(page, 'sites-mobile-drawer.png')
})

test('administrator configures, previews, publishes, and restores a self-correcting status layout', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/administration/interface?view=layout')
  await expect(page.getByRole('heading', { name: 'Administration', exact: true })).toBeVisible()
  await expect(page.getByText('Monitoring remains active', { exact: true })).toBeVisible()
  await captureStatusLayout(page, 'default-desktop.png')

  const energy = page.locator('.status-config-list > article[data-indicator-key="data.energy_today"]')
  await energy.getByRole('checkbox', { name: 'Show Energy today' }).click()
  await expect(page.getByRole('heading', { name: 'Disabled indicators' })).toBeVisible()
  await expect(page.locator('.disabled-indicators [data-indicator-key="data.energy_today"]')).toHaveCount(0)
  await expect(page.locator('.disabled-indicator-grid').getByText('Energy today', { exact: true })).toBeVisible()

  const offline = page.locator('.status-config-list > article[data-indicator-key="device.offline_count"]')
  await offline.getByLabel('Zone').selectOption('page_summary')
  await offline.getByRole('button', { name: 'Move to beginning' }).click()
  await expect(page.getByText('Offline or stale moved first.')).toBeAttached()

  await page.getByLabel('Viewport').selectOption('tablet')
  await expect(page.getByTestId('status-layout-preview')).toHaveClass(/preview-tablet/)
  await captureStatusLayout(page, 'default-tablet.png')
  await page.getByLabel('Viewport').selectOption('mobile')
  await expect(page.getByTestId('status-layout-preview')).toHaveClass(/preview-mobile/)
  await captureStatusLayout(page, 'default-mobile-preview.png')
  await page.getByLabel('Scenario').selectOption('empty_zone')
  expect(
    await page
      .getByTestId('status-layout-preview')
      .locator('[data-status-zone]')
      .evaluateAll((zones) => zones.every((zone) => zone.children.length > 0)),
  ).toBe(true)
  await captureStatusLayout(page, 'empty-zone-mobile.png')
  await page.getByLabel('Viewport').selectOption('desktop')
  await page.getByLabel('Scenario').selectOption('long_label')
  await expect(page.getByText(/deliberately long translated indicator label/i)).toBeVisible()
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%' })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
  await page.evaluate(() => { document.documentElement.style.fontSize = '' })
  await captureStatusLayout(page, 'long-label-desktop.png')

  await page.getByRole('button', { name: 'Save draft' }).first().click()
  await expect(page.getByText(/Draft revision 1 saved/)).toBeVisible()
  await page.getByLabel('Scenario').selectOption('all_defaults')
  await page.getByRole('button', { name: 'Refresh preview' }).click()
  await expect(page.getByText('Current draft previewed')).toBeVisible()
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: 'Publish layout' }).click()
  await expect(page.getByText(/Published layout is active/)).toBeVisible()

  await page.goto('/')
  await expect(page.locator('[data-indicator-key="data.energy_today"]')).toHaveCount(0)
  await expect(page.locator('[data-indicator-key="device.offline_count"]')).toBeVisible()
  expect(
    await page
      .locator('[data-status-zone]')
      .evaluateAll((zones) => zones.every((zone) => zone.children.length > 0)),
  ).toBe(true)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
  await page.goto('/alerts')
  await expect(page.getByText('Synchronization backlog')).toBeVisible()

  await page.goto('/administration/interface?view=layout')
  const revisionOne = page.locator('.revision-list article').filter({ hasText: 'Revision 1' })
  page.once('dialog', (dialog) => dialog.accept())
  await revisionOne.getByRole('button', { name: 'Restore' }).click()
  await expect(page.getByText(/restored as a new immutable revision/)).toBeVisible()
  await page.goto('/')
  await expect(page.locator('[data-indicator-key="data.energy_today"]')).toBeVisible()

  await page.setViewportSize({ width: 375, height: 760 })
  await page.reload()
  await expect(page.getByRole('button', { name: /More status/ })).toBeVisible()
  await page.getByRole('button', { name: /More status/ }).click()
  await expect(page.locator('[data-status-zone="mobile_status_drawer"]')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Power Dashboard' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
  await captureStatusLayout(page, 'published-mobile.png')
})

test('normal pages stay compact, metrics are unique, and System Health owns diagnostics', async ({ page }) => {
  await mockApplication(page)
  const normalRoutes = [
    '/overview',
    '/monitoring/devices',
    '/monitoring/topology',
    '/analytics/history',
    '/billing/rate-plans',
    '/alerts',
    '/monitoring/enrollment',
    '/administration/sites-network',
    '/administration/access',
    '/administration/interface?view=text',
    '/administration/interface?view=layout',
  ]
  for (const route of normalRoutes) {
    await page.goto(route)
    await expect(page.locator('[data-status-zone="global_status_row"]')).toHaveCount(0)
    await expect(page.locator('[data-status-zone] [data-indicator-key="system.api_health"]')).toHaveCount(0)
    await expect(page.locator('[data-status-zone] [data-indicator-key="system.database_health"]')).toHaveCount(0)
    await expect(page.locator('[data-status-zone] [data-indicator-key="system.worker_health"]')).toHaveCount(0)
    const metricIdentities = await page.locator('[data-metric-identity]').evaluateAll((elements) => elements.map((element) => element.getAttribute('data-metric-identity')).filter(Boolean))
    expect(metricIdentities).toEqual([...new Set(metricIdentities)])
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
  }

  await page.goto('/')
  await expect(page.getByRole('region', { name: 'overview summary' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Device contribution' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Operational status' })).toBeVisible()
  await captureStatusLayout(page, 'overview-reporting-desktop.png')

  await page.goto('/analytics/history')
  await expect(page.locator('[data-metric-identity="data.coverage"]')).toHaveCount(1)
  await expect(page.locator('[data-metric-identity="power.recent_peak"]')).toHaveCount(1)
  await expect(page.locator('[data-indicator-key="rate.current_context"]')).toHaveCount(1)
  await captureStatusLayout(page, 'history-data-desktop.png')

  await page.goto('/monitoring/enrollment')
  await expect(page.getByRole('heading', { name: 'Monitoring', exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Enrollment' })).toHaveAttribute('aria-selected', 'true')
  await captureStatusLayout(page, 'enrollment-desktop.png')

  await page.goto('/administration/security?view=health')
  await expect(page.getByRole('heading', { name: 'Administration' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'System Health' })).toHaveAttribute('aria-current', 'page')
  await expect(page.locator('[data-status-zone="administration_diagnostics"]')).toBeVisible()
  await expect(page.locator('[data-indicator-key="system.api_health"]')).toBeVisible()
  await expect(page.locator('[data-indicator-key="system.database_health"]')).toBeVisible()
  await expect(page.locator('[data-indicator-key="system.worker_health"]')).toBeVisible()
  await captureStatusLayout(page, 'system-health-desktop.png')
})

test('System Health makes an operational failure visible without restoring the global health row', async ({ page }) => {
  await mockApplication(page)
  await page.route('**/api/v1/status-indicators/values*', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        registry_version: 'status-indicators/1.0',
        generated_at: '2026-07-20T19:05:00Z',
        values: {
          ...statusValues,
          'system.worker_health': {
            status: 'failed',
            severity: 'critical',
            display_value: 'Failed',
            detail: 'The most recent worker loop failed.',
            freshness_at: '2026-07-20T19:05:00Z',
          },
        },
      }),
    })
  })
  await page.goto('/administration/security?view=health')
  const worker = page.locator('[data-indicator-key="system.worker_health"]')
  await expect(worker).toContainText('Failed')
  await expect(worker).toHaveClass(/severity-critical/)
  await expect(page.getByRole('link', { name: /Open alerts/ })).toBeVisible()
  await expect(page.locator('[data-status-zone="global_status_row"]')).toHaveCount(0)
  await captureStatusLayout(page, 'system-health-failure.png')
})

test('Overview and History use one clear no-data state and preserve measured zero', async ({ page }) => {
  await mockApplication(page)
  await page.route('**/api/v1/fleet/summary*', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...fleet, current_load_w: '0', recent_peak_w: '0', energy_today_kwh: '0', estimated_cost_today: '0', billing_cycle_energy_kwh: '0', estimated_billing_cycle_cost: '0', online_devices: 0, synchronized_devices: 0, total_devices: 0, active_alerts: 0, current_tou_bucket: null, has_live_data: false, has_energy_data: false, has_cost_data: false, reporting_devices: 0, latest_heartbeat_at: null, coverage_percent: null }) })
  })
  await page.route('**/api/v1/devices', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: '[]' })
  })
  await page.goto('/')
  await expect(page.getByText('No sensors enrolled', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Site Summary' })).toHaveCount(0)
  await expect(page.getByText('$0.00')).toHaveCount(0)
  await expect(page.locator('.overview-onboarding .state-card')).toHaveCount(1)
  await captureStatusLayout(page, 'overview-no-sensors.png')

  await page.unroute('**/api/v1/fleet/summary*')
  await page.unroute('**/api/v1/devices')
  await page.route('**/api/v1/fleet/summary*', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...fleet, current_load_w: '0', recent_peak_w: '0', has_live_data: true, reporting_devices: 1 }) })
  })
  await page.route('**/api/v1/devices', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify([{ ...device, current_watts: '0' }]) })
  })
  await page.goto('/')
  await expect(page.locator('.power-hero')).toContainText('0')
  await expect(page.locator('.power-hero')).not.toContainText('Unavailable')

  await page.route('**/api/v1/history/query', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ scope: { type: 'device', display_name: 'Garage HVAC', site_id: 'site-1', site_name: 'Upland Site', timezone: 'America/Los_Angeles', included_device_ids: ['device-1'], included_device_names: ['Garage HVAC'], excluded_device_ids: [], mixed_rates: false }, display_mode: 'combined', metrics: ['energy_kwh', 'energy_cost'], bucket: '1h', summary: { start_utc: '2026-07-21T03:00:00Z', end_utc: '2026-07-21T05:00:00Z', coverage_percent: '0', contributing_sensor_count: 0, tou_breakdown: {} }, combined: [], individual: [], rate_versions_used: [], warnings: [], total_buckets: 0, page: 1, page_size: 100 }) })
  })
  await page.goto('/analytics/history')
  await expect(page.getByText('No readings in this range', { exact: true })).toBeVisible()
  await expect(page.locator('.history-summary-grid')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Interval details' })).toHaveCount(0)
  await expect(page.getByText('$0.00')).toHaveCount(0)
  await captureStatusLayout(page, 'history-no-data.png')
})
