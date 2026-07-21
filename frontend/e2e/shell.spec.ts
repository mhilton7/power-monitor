import { expect, test, type Page } from '@playwright/test'
import { emptyRateDocument } from '../src/rates'

const session = (roles: string[] = ['admin']) => ({
  authenticated: true,
  bootstrap_required: false,
  user: { id: 'user-1', email: 'owner@example.test', display_name: 'Fleet Owner', roles },
})

const site = { id: 'site-1', name: 'Upland Site', timezone: 'America/Los_Angeles', allowed_cidrs: [], allowed_domains: [], allow_public_polling: false }
const fleet = { current_load_w: '960', energy_today_kwh: '12.5', estimated_cost_today: '4.25', billing_cycle_energy_kwh: '244', estimated_billing_cycle_cost: '83.11', online_devices: 1, synchronized_devices: 1, total_devices: 1, active_alerts: 1, current_tou_bucket: 'on-peak', recent_peak_w: '1800', disclosure: 'Estimate, not utility bill.' }
const device = { id: 'device-1', name: 'Garage HVAC', site_id: 'site-1', site_name: 'Upland Site', circuit_id: 'branch-1', circuit_name: 'Garage branch', connection_mode: 'hybrid', measurement_role: 'submeter', cost_scope: 'energy_only', included_in_default: true, ct_rating_amps: '100', status: 'online_synchronized', lifecycle_status: 'active', current_watts: '960', last_seen_at: '2026-07-20T06:00:00Z', firmware_version: '1.0.0', rssi_dbm: -52, pzem_ok: true, sd_ok: true, time_trusted: true, backlog: 0 }
const historyDevice = { ...device, id: 'device-2', hardware_id: 'esp32-main-leg-2', name: 'Main Panel L2', circuit_id: 'leg-2', circuit_name: 'Main Panel L2', measurement_role: 'service-leg', current_watts: '1000' }
const officialVersion = { id: 'rate-version-official', version: 1, effective_from: '2026-06-01', effective_through: null, status: 'active', source_kind: 'official_sce', source_checked_at: '2026-07-19T10:15:00Z', source_label: 'SCE archived evidence', integrity_sha256: 'a'.repeat(64), is_active: true, immutable: true, created_at: '2026-06-01T00:00:00Z' }
const officialPlan = { id: 'rate-plan-official', code: 'TOU-D-4-9PM', name: 'TOU-D 4 PM to 9 PM', description: 'Official SCE residential time-of-use plan.', plan_kind: 'official_sce', ownership_scope: 'global', currency: 'USD', timezone: 'America/Los_Angeles', status: 'active', versions: [officialVersion] }
const accessPermissions = [
  ['overview.view', 'Dashboard', 'View overview', false], ['sites.view', 'Sites', 'View sites', false],
  ['devices.view', 'Devices', 'View devices', false], ['devices.manage', 'Devices', 'Manage devices', false],
  ['users.view', 'Administration', 'View users', true], ['users.manage', 'Administration', 'Manage users', true],
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

const statusZones = ['global_header_left', 'global_header_center', 'global_header_right', 'global_status_row', 'sidebar_upper', 'sidebar_lower', 'global_footer', 'page_header_primary', 'page_header_secondary', 'page_status_row', 'page_summary_strip', 'page_footer', 'mobile_header', 'mobile_status_strip', 'mobile_status_drawer']
const statusPages = ['overview', 'devices', 'device_detail', 'topology', 'history', 'rates', 'rate_sources', 'alerts', 'enrollment', 'administration', 'backups']
const statusDefinition = (key: string, label: string, category: string, zone: string, order: number, pages = statusPages, permission = 'overview.view', criticalFallback?: string) => ({
  key, default_label: label, description: `${label} status from existing server data.`, category, data_source: key, current_value_schema: { status: 'string', display_value: 'string' }, severity_capability: ['info', 'success', 'warning', 'critical', 'unknown'], default_enabled: true, default_zone: zone, allowed_zones: zone.startsWith('global') ? [...statusZones.filter((item) => item.startsWith('global')), ...statusZones.filter((item) => item.startsWith('mobile'))] : [...statusZones.filter((item) => item.startsWith('page')), ...statusZones.filter((item) => item.startsWith('mobile'))], default_order: order, supported_pages: pages, global_shell_support: zone.startsWith('global'), minimum_display_width: 140, preferred_display_width: 220, presentations: ['compact', 'standard', 'detailed'], icon_supported: true, label_supported: true, value_supported: true, freshness_supported: true, role_visibility_supported: true, permission_required: permission, configurable: true, critical_fallback: criticalFallback, renderer: key.includes('power') || key.includes('peak') ? 'power' : key.includes('count') || key.includes('online') || key.includes('offline') ? 'count' : 'health', icon: key.includes('alert') ? 'bell' : key.includes('power') ? 'zap' : 'activity', registry_version: 'status-indicators/1.0',
})
const statusDefinitions = [
  statusDefinition('data.live_connection', 'Live data', 'Live data', 'global_header_center', 10, statusPages, 'overview.view', 'Device disconnect alerts remain active.'),
  statusDefinition('data.current_power', 'Current load', 'Live data', 'global_header_center', 20),
  statusDefinition('alerts.active_count', 'Active alerts', 'Alerts', 'global_header_right', 10, statusPages, 'alerts.view', 'Alerts remain on Alerts & Notifications.'),
  statusDefinition('alerts.critical_count', 'Critical alerts', 'Alerts', 'page_summary_strip', 10, ['alerts'], 'alerts.view', 'Critical alerts remain in the timeline.'),
  statusDefinition('alerts.warning_count', 'Warning alerts', 'Alerts', 'page_summary_strip', 20, ['alerts'], 'alerts.view'),
  statusDefinition('alerts.enabled_rule_count', 'Rules enabled', 'Alerts', 'page_summary_strip', 30, ['alerts'], 'alerts.view'),
  statusDefinition('alerts.disconnect_rule_state', 'Disconnect alerts', 'Alerts', 'page_summary_strip', 40, ['alerts'], 'alerts.view', 'Heartbeat monitoring remains active.'),
  statusDefinition('device.online_count', 'Devices online', 'Devices', 'page_status_row', 10, ['overview', 'devices'], 'devices.view', 'Device status remains on Devices.'),
  statusDefinition('device.offline_count', 'Offline or stale', 'Devices', 'page_status_row', 20, ['overview', 'devices'], 'devices.view', 'Disconnect alerts remain active.'),
  statusDefinition('data.energy_today', 'Energy today', 'Energy', 'page_summary_strip', 10, ['overview']),
  statusDefinition('rate.current_period', 'Current rate period', 'Rates', 'page_summary_strip', 20, ['overview', 'rates', 'history'], 'rates.view'),
  statusDefinition('rate.source_health', 'Rate source health', 'Rates', 'page_status_row', 10, ['rates', 'rate_sources'], 'rates.view'),
  statusDefinition('rate.update_pending', 'Rate update pending', 'Rates', 'page_status_row', 20, ['rates', 'rate_sources'], 'rates.view'),
  statusDefinition('rate.last_successful_check', 'Last source check', 'Rates', 'page_summary_strip', 10, ['rates', 'rate_sources'], 'rates.manage_sources'),
  statusDefinition('rate.next_scheduled_check', 'Next source check', 'Rates', 'page_summary_strip', 20, ['rates', 'rate_sources'], 'rates.manage_sources'),
  statusDefinition('rate.review_policy', 'Review policy', 'Rates', 'page_summary_strip', 30, ['rates', 'rate_sources'], 'rates.manage_sources'),
  statusDefinition('data.recent_peak', 'Recent peak', 'Energy', 'page_summary_strip', 30, ['overview', 'history']),
  statusDefinition('system.worker_health', 'Worker', 'System', 'global_status_row', 10, statusPages, 'settings.view', 'Worker alerts and diagnostics remain active.'),
]
const defaultStatusConfiguration = () => ({
  schema_version: 'power-monitor-status-layout/1.0' as const,
  registry_version: 'status-indicators/1.0', personalization_enabled: false as const,
  items: statusDefinitions.map((definition) => ({ indicator_key: definition.key, page: '*', role: '*', breakpoint: 'default', visible: true, zone: definition.default_zone, order: definition.default_order, density: 'standard', show_icon: true, show_label: true, show_value: true, show_freshness: true, show_severity: true, show_tooltip: true })),
})
const statusValues = Object.fromEntries(statusDefinitions.map((definition) => [definition.key, { status: 'healthy', severity: 'success', display_value: definition.key === 'data.current_power' ? '960 W' : definition.key === 'alerts.active_count' ? '1' : definition.key === 'device.online_count' ? '1' : definition.key === 'device.offline_count' ? '0' : definition.key === 'data.energy_today' ? '12.50 kWh' : definition.key === 'rate.current_period' ? 'On-peak' : definition.key === 'data.recent_peak' ? '1,800 W' : 'Healthy', detail: `${definition.default_label} detail`, freshness_at: '2026-07-20T19:05:00Z' }]))

function resolveMockStatus(configuration: ReturnType<typeof defaultStatusConfiguration>, pageName: string, breakpoint: string, role = 'admin') {
  const items = statusDefinitions.flatMap((definition) => {
    if (!definition.global_shell_support && !definition.supported_pages.includes(pageName)) return []
    const matches = configuration.items.filter((item) => item.indicator_key === definition.key && (item.page === '*' || item.page === pageName) && (item.role === '*' || item.role === role) && (item.breakpoint === 'default' || item.breakpoint === breakpoint))
    const state = matches.reduce((value, item) => ({ ...value, ...item }), configuration.items.find((item) => item.indicator_key === definition.key) ?? { visible: definition.default_enabled, zone: definition.default_zone, order: definition.default_order })
    if (!state.visible) return []
    let zone = state.zone
    if (breakpoint === 'mobile' && !matches.some((item) => item.breakpoint === 'mobile' && item.zone)) zone = zone.startsWith('global_header') ? 'mobile_header' : zone === 'global_status_row' || zone === 'page_status_row' ? 'mobile_status_strip' : 'mobile_status_drawer'
    return [{ ...state, indicator_key: definition.key, zone, definition }]
  })
  return { schema_version: 'power-monitor-status-layout/1.0', registry_version: 'status-indicators/1.0', published_revision: 1, page: pageName, roles: [role], breakpoint, zones: statusZones.map((key) => ({ key, items: items.filter((item) => item.zone === key).sort((a, b) => Number(a.order) - Number(b.order)) })).filter((zone) => zone.items.length), warnings: [], personalization_enabled: false }
}

async function mockApplication(page: Page, roles: string[] = ['admin']) {
  let enrollmentCounter = 0
  let sensorRemoved = false
  let customDocument: Record<string, unknown> | undefined
  let customVersionStatus = 'draft'
  let candidateStatus = 'pending_review'
  let jobPolls = 0
  let userAccessRevision = 1
  let managedUserRoles = ['viewer']
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
  const statusRevisions = [{ id: 'status-revision-1', revision: 1, registry_version: 'status-indicators/1.0', created_by: 'user-1', created_at: '2026-07-20T10:00:00Z', reason: 'Compiled current layout' }]
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    let body: unknown = []
    if (path === '/api/v1/auth/session') body = session(roles)
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
      if (previewRequest.scenario === 'long_label' && firstPreviewItem) firstPreviewItem.definition = { ...firstPreviewItem.definition, default_label: 'A deliberately long translated indicator label that wraps safely' }
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
      { ...session(roles).user, is_active: true, status: 'active', all_sites: true, sites: [], site_ids: [], permissions: accessPermissions.map((item) => item.code), permission_count: accessPermissions.length, mfa_enabled: true, last_login_at: '2026-07-20T10:00:00Z', active_session_count: 1, created_at: '2026-07-01T00:00:00Z', access_revision: 1, protected_administrator: true },
      { id: 'user-2', email: 'viewer@example.test', display_name: 'Dashboard Viewer', roles: managedUserRoles, is_active: true, status: 'active', all_sites: false, sites: [site], site_ids: ['site-1'], permissions: managedUserRoles.includes('operator') ? ['overview.view', 'sites.view', 'devices.view', 'devices.manage'] : ['overview.view', 'sites.view', 'devices.view'], permission_count: managedUserRoles.includes('operator') ? 4 : 3, mfa_enabled: false, last_login_at: '2026-07-19T10:00:00Z', active_session_count: 2, created_at: '2026-07-02T00:00:00Z', access_revision: userAccessRevision, protected_administrator: false },
    ] }
    else if (path === '/api/v1/admin/users/user-2' && request.method() === 'GET') body = { id: 'user-2', email: 'viewer@example.test', display_name: 'Dashboard Viewer', roles: managedUserRoles, is_active: true, status: 'active', all_sites: false, sites: [site], site_ids: ['site-1'], permissions: managedUserRoles.includes('operator') ? ['overview.view', 'sites.view', 'devices.view', 'devices.manage'] : ['overview.view', 'sites.view', 'devices.view'], permission_count: managedUserRoles.includes('operator') ? 4 : 3, mfa_enabled: false, last_login_at: '2026-07-19T10:00:00Z', active_session_count: 2, created_at: '2026-07-02T00:00:00Z', access_revision: userAccessRevision, protected_administrator: false, sessions: [{ id: 'session-2', created_at: '2026-07-20T08:00:00Z', last_seen_at: '2026-07-20T10:00:00Z', expires_at: '2026-07-21T10:00:00Z', source_ip: '192.168.0.20', user_agent: 'Browser' }], permission_sources: { viewer: ['overview.view', 'sites.view', 'devices.view'] } }
    else if (path === '/api/v1/admin/users/user-2/access' && request.method() === 'PUT') { const update = JSON.parse(request.postData() ?? '{}') as { role_ids: string[] }; managedUserRoles = update.role_ids; userAccessRevision += 1; body = { id: 'user-2', email: 'viewer@example.test', display_name: 'Dashboard Viewer', roles: managedUserRoles, is_active: true, status: 'active', all_sites: false, sites: [site], site_ids: ['site-1'], permissions: ['overview.view', 'sites.view', 'devices.view', 'devices.manage'], permission_count: 4, mfa_enabled: false, active_session_count: 0, created_at: '2026-07-02T00:00:00Z', access_revision: userAccessRevision, protected_administrator: false, sessions_revoked: 2 } }
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
    else if (path === '/api/v1/sites') body = [site]
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

test('viewer sees fleet evidence and an in-app denial for restricted pages', async ({ page }) => {
  await mockApplication(page, ['viewer'])
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Power Dashboard' })).toBeVisible()
  await expect(page.getByText('960 W', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('link', { name: /Enroll sensor/ })).toHaveCount(0)
  await page.goto('/enrollment')
  await expect(page.getByRole('heading', { name: 'Access denied' })).toBeVisible()
  await page.goto('/admin')
  await expect(page.getByRole('heading', { name: 'Access denied' })).toBeVisible()
})

test('history combines sensors, shows TOU cost provenance, selects a range, and exports CSV', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/history')
  await expect(page.getByRole('heading', { name: 'History & comparison' })).toBeVisible()
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
  await page.goto('/topology')
  await expect(page.getByRole('heading', { name: 'Site & circuit topology' })).toBeVisible()
  await expect(page.getByText('Potential overlap was explicitly confirmed.')).toBeVisible()
  await expect(page.getByText('Never summed by default')).toBeVisible()
})

test('cost workspace is removed from navigation and routing', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/')
  await expect(page.getByRole('link', { name: 'Costs' })).toHaveCount(0)
  await page.goto('/costs')
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole('heading', { name: 'Power Dashboard' })).toBeVisible()
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

test('administrator can create and remove local users', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/admin')
  await page.getByRole('button', { name: /Add user/ }).click()
  await page.getByLabel('Display name').fill('Energy Analyst')
  await page.getByLabel('Email address').fill('analyst@example.test')
  await page.getByLabel('Temporary password').fill('Production-Password-42!')
  const createRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/users') && request.method() === 'POST')
  await page.getByRole('button', { name: /Create user/ }).click()
  await createRequest

  const viewerRow = page.getByRole('row').filter({ hasText: 'Dashboard Viewer' })
  const removeRequest = page.waitForRequest((request) => request.url().endsWith('/api/v1/users/user-2') && request.method() === 'DELETE')
  await viewerRow.getByRole('button', { name: 'Remove' }).click()
  await viewerRow.getByRole('button', { name: 'Remove', exact: true }).click()
  await removeRequest
})

test('administrator previews effective access and promotes a site-scoped user', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/administration/users-access')
  await expect(page.getByRole('heading', { name: 'Users & Access' })).toBeVisible()
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
  await expect(page.getByRole('heading', { name: 'Dashboard & Login Text' })).toBeVisible()
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
  await page.goto('/admin')
  await page.getByRole('tab', { name: 'Notifications' }).click()
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
  await page.goto('/rates')
  await expect(page.getByRole('heading', { name: 'Rate plans' })).toBeVisible()
  await page.getByRole('button', { name: /Custom plan/ }).click()
  await page.getByLabel('Plan name').fill('Weekday and weekend test plan')
  await page.getByLabel('Plan code').fill('CUSTOM-E2E')
  await page.getByRole('button', { name: /Next/ }).click()
  await expect(page.getByRole('button', { name: /Seasons & schedules/ })).toHaveAttribute('aria-current', 'step')
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
  await expect(page).toHaveURL(/\/rates\/custom-plan-1\/versions\/custom-version-1/)
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
  await page.goto('/rates/sources')
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
  await page.goto('/rates/sources')
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
  await page.goto('/rates/sources')
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

test('administration cards and status pills stay inside settings panels', async ({ page }) => {
  await page.setViewportSize({ width: 1512, height: 768 })
  await mockApplication(page)
  await page.goto('/admin')
  await page.getByRole('tab', { name: 'Sites & accounts' }).click()
  const siteCard = page.locator('.admin-card').first()
  const sitePanel = siteCard.locator('xpath=ancestor::section[1]')
  const [siteCardBox, sitePanelBox, siteStatusBox] = await Promise.all([
    siteCard.boundingBox(),
    sitePanel.boundingBox(),
    siteCard.locator('.status').boundingBox(),
  ])
  expect(siteCardBox).not.toBeNull()
  expect(sitePanelBox).not.toBeNull()
  expect(siteStatusBox).not.toBeNull()
  expect((siteCardBox?.x ?? 0) + (siteCardBox?.width ?? 0)).toBeLessThanOrEqual((sitePanelBox?.x ?? 0) + (sitePanelBox?.width ?? 0))
  expect((siteStatusBox?.x ?? 0) + (siteStatusBox?.width ?? 0)).toBeLessThanOrEqual((siteCardBox?.x ?? 0) + (siteCardBox?.width ?? 0))

  await page.getByRole('tab', { name: 'Diagnostics' }).click()
  const diagnostic = page.locator('.diagnostic-hero')
  const [diagnosticBox, diagnosticPanelBox, diagnosticStatusBox] = await Promise.all([
    diagnostic.boundingBox(),
    diagnostic.locator('xpath=ancestor::section[1]').boundingBox(),
    diagnostic.locator('.status').boundingBox(),
  ])
  expect((diagnosticBox?.x ?? 0) + (diagnosticBox?.width ?? 0)).toBeLessThanOrEqual((diagnosticPanelBox?.x ?? 0) + (diagnosticPanelBox?.width ?? 0))
  expect((diagnosticStatusBox?.x ?? 0) + (diagnosticStatusBox?.width ?? 0)).toBeLessThanOrEqual((diagnosticBox?.x ?? 0) + (diagnosticBox?.width ?? 0))
  const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)
  expect(horizontalOverflow).toBe(false)
})

test('disabled controls in rate details use a disabled cursor, not a busy spinner', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/rates')
  await page.getByRole('button', { name: 'View details' }).click()
  const previous = page.getByRole('button', { name: 'Previous' })
  await expect(previous).toBeDisabled()
  await expect(previous).toHaveCSS('cursor', 'not-allowed')
  const disabledCursors = await page.locator('.button:disabled').evaluateAll((buttons) =>
    buttons.map((button) => getComputedStyle(button).cursor),
  )
  expect(disabledCursors).not.toContain('wait')
})

test('administrator downloads a seven-day application-log export from Backups', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/admin')
  await page.getByRole('tab', { name: 'Backups' }).click()
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
  await page.goto('/devices')
  await expect(page.getByRole('heading', { name: 'Device Management' })).toBeVisible()
  await expect(page.getByText('Sensor health and general data')).toBeVisible()
  await captureDashboardCorrection(page, 'device-management.png')
  await expect(page.getByText(/pm-protocol\/1\.0\.0/i)).toHaveCount(0)
  await expect(page.getByText(/server protected/i)).toHaveCount(0)
  await expect(page.getByText(/multi-sensor fleet/i)).toHaveCount(0)
  await expect(page.getByText(/signed heartbeats.*local custody/i)).toHaveCount(0)
  await page.goto('/alerts')
  await expect(page.getByRole('heading', { name: 'Alerts & Notifications' })).toBeVisible()
  await expect(page).toHaveTitle('Alerts & Notifications · Power Monitor')
  await expect(page.getByText(/Evidence, debounce, resolution, acknowledgement/)).toHaveCount(0)
  await captureDashboardCorrection(page, 'alerts-and-notifications.png')
})

test('search and dropdown controls keep compact pointer focus and visible keyboard focus', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/devices')
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
  await expect(page.getByRole('link', { name: 'Devices' })).toBeVisible()
})

test('administrator configures, previews, publishes, and restores a self-correcting status layout', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/administration/status-indicators')
  await expect(page.getByRole('heading', { name: 'Status Indicators & Layout' })).toBeVisible()
  await expect(page.getByText('Monitoring remains active', { exact: true })).toBeVisible()
  await captureStatusLayout(page, 'default-desktop.png')

  const energy = page.locator('.status-config-list > article[data-indicator-key="data.energy_today"]')
  await energy.getByRole('checkbox', { name: 'Show Energy today' }).click()
  await expect(page.getByRole('heading', { name: 'Disabled indicators' })).toBeVisible()
  await expect(page.locator('.disabled-indicators [data-indicator-key="data.energy_today"]')).toHaveCount(0)
  await expect(page.locator('.disabled-indicator-grid').getByText('Energy today', { exact: true })).toBeVisible()

  const offline = page.locator('.status-config-list > article[data-indicator-key="device.offline_count"]')
  await offline.getByLabel('Zone').selectOption('page_summary_strip')
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
  const zoomOverflow = await page.locator('body *').evaluateAll((elements) => elements
    .filter((element) => element.getBoundingClientRect().right > document.documentElement.clientWidth + 1)
    .slice(0, 10)
    .map((element) => `${element.tagName.toLowerCase()}.${element.getAttribute('class') ?? ''}: ${element.textContent?.trim().slice(0, 60)}`))
  expect(zoomOverflow).toEqual([])
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

  await page.goto('/administration/status-indicators')
  const revisionOne = page.locator('.revision-list article').filter({ hasText: 'Revision 1' })
  page.once('dialog', (dialog) => dialog.accept())
  await revisionOne.getByRole('button', { name: 'Restore' }).click()
  await expect(page.getByText(/restored as a new immutable revision/)).toBeVisible()
  await page.goto('/')
  await expect(page.locator('[data-indicator-key="data.energy_today"]')).toBeVisible()

  await page.setViewportSize({ width: 375, height: 760 })
  await page.reload()
  await expect(page.locator('[data-status-zone="mobile_header"]')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Power Dashboard' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
  await captureStatusLayout(page, 'published-mobile.png')
})
