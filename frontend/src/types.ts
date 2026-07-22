export type BuiltInRole = 'admin' | 'operator' | 'rate-manager' | 'viewer'
export type Role = string

export interface User {
  id: string
  email: string
  display_name: string
  roles: Role[]
  permissions?: string[]
  all_sites?: boolean
  site_ids?: string[]
}

export interface Session {
  authenticated: boolean
  user?: User
  expires_at?: string
  csrf_token?: string
  bootstrap_required: boolean
}

export interface Site {
  id: string
  name: string
  timezone: string
  allowed_cidrs: string[]
  allowed_domains: string[]
  allow_public_polling: boolean
}

export interface UtilityAccountRateContext {
  state: string
  current_plan?: string
  plan_code?: string
  current_version?: number
  rate_version_id?: string
  current_period?: string
  current_price_per_kwh?: string
  next_period?: string
  next_price_per_kwh?: string
  next_period_at?: string
  current_currency: string
  billing_cycle?: { starts_at: string; ends_at: string }
  assignment_effective_from?: string
  source_type?: string
  source_checked_at?: string
  next_assignment?: { rate_version_id: string; plan: string; effective_from: string }
}

export interface UtilityAccount {
  id: string
  site_id: string
  site_name: string
  utility_id: string
  utility_name: string
  name: string
  nickname?: string
  account_number_suffix?: string
  status: 'active' | 'archived'
  timezone: string
  currency: string
  billing_cycle_start_day: number
  baseline_allocation_kwh?: string
  generation_provider: string
  provider_mode: string
  service_class?: string
  cost_scope: 'energy_only' | 'allocated_account_estimate' | 'full_account_estimate'
  allocation_method?: string
  full_account_override: boolean
  revision: number
  archived_at?: string
  rate_context: UtilityAccountRateContext
  assignment_count: number
  device_count: number
  readiness: { rate: string; cost: string; topology_complete: boolean }
}

export interface SensorNetworkCidr {
  id: string
  network: string
  label: string
  enabled: boolean
  revision: number
}

export interface SensorNetworkPolicy {
  id: string
  site_id: string
  site_name: string
  direction: 'device_ingress' | 'server_pull'
  mode: 'allow_listed_private' | 'allow_all_private' | 'deny_all' | 'legacy_authenticated_any' | 'legacy_public_and_listed'
  revision: number
  migration_notice_pending: boolean
  migrated_from_legacy: boolean
  effective_summary: string
  cidrs: SensorNetworkCidr[]
}

export interface Device {
  id: string
  name: string
  site_id: string
  site_name?: string
  circuit_id?: string
  circuit_name?: string
  connection_mode: 'pull' | 'push' | 'hybrid'
  measurement_role: string
  cost_scope: 'energy_only' | 'allocated_account' | 'full_account'
  included_in_default: boolean
  ct_rating_amps: string
  status: string
  last_seen_at?: string
  firmware_version?: string
  current_watts?: string
  rssi_dbm?: number
  pzem_ok?: boolean
  sd_ok?: boolean
  time_trusted?: boolean
  backlog: number
  lifecycle_status?: 'active' | 'decommissioned'
  decommissioned_at?: string
  decommissioned_by?: string
  decommissioned_by_name?: string
  decommission_reason?: string
  removed_site_id?: string
  removed_circuit_id?: string
  removed_circuit_name?: string
  retained_history?: boolean
  re_enrollment_allowed?: boolean
}

export interface FleetSummary {
  site_id?: string
  current_load_w: string
  energy_today_kwh: string
  estimated_cost_today: string
  billing_cycle_energy_kwh: string
  estimated_billing_cycle_cost: string
  online_devices: number
  synchronized_devices: number
  total_devices: number
  active_alerts: number
  current_tou_bucket?: string
  current_rate_plan?: string
  current_rate_version?: number
  current_rate_price_per_kwh?: string
  rate_configured?: boolean
  recent_peak_w: string
  has_live_data?: boolean
  has_energy_data?: boolean
  has_cost_data?: boolean
  reporting_devices?: number
  latest_heartbeat_at?: string
  coverage_percent?: string
  disclosure: string
}

export interface ApiProblem {
  title: string
  detail: string
  status: number
  code: string
  errors?: Array<{ location: string[]; message: string }>
  warnings?: string[]
}

export interface Circuit {
  id: string
  site_id: string
  parent_id?: string
  name: string
  measurement_role: string
  split_phase_group?: string
}

export interface AggregateSet {
  id: string
  site_id: string
  name: string
  cost_scope: string
  is_default: boolean
  members: Array<{ circuit_id?: string; device_id?: string; allocation_percent: string }>
  overlap_confirmed_at?: string
}

export type HistoryScopeType = 'device' | 'devices' | 'circuit' | 'site' | 'aggregate_set'
export type HistoryDisplayMode = 'combined' | 'individual' | 'combined_plus_individual'
export type HistoryMetric = 'power_w' | 'energy_kwh' | 'voltage_v' | 'current_a' | 'power_factor' | 'frequency_hz' | 'energy_cost' | 'usage_cost'

export interface HistoryScopeRequest {
  type: HistoryScopeType
  device_id?: string
  device_ids?: string[]
  circuit_id?: string
  site_id?: string
  aggregate_set_id?: string
}

export interface HistoryQueryRequest {
  scope: HistoryScopeRequest
  display_mode: HistoryDisplayMode
  metrics: HistoryMetric[]
  start_utc: string
  end_utc: string
  bucket: 'auto' | 'raw' | '5m' | '15m' | '1h' | '1d'
  timezone?: string
  strict_coverage: boolean
  selection_start_utc?: string
  selection_end_utc?: string
  page: number
  page_size: number
}

export interface HistoryRateContribution {
  utility_account_id: string
  rate_plan_id: string
  rate_plan_name: string
  rate_version_id: string
  rate_version: number
  rate_effective_from: string
  tou_period: string
  energy_kwh: string
  rate_per_kwh: string
  energy_cost: string
}

export interface HistoryBucket {
  interval_start_utc: string
  interval_end_utc: string
  local_start: string
  local_end: string
  utc_offset: string
  series_id: string
  series_name: string
  device_id?: string
  included_sensor_count: number
  contributing_sensor_count: number
  energy_kwh?: string
  average_power_w?: string
  peak_power_w?: string
  voltage_min_v?: string
  voltage_avg_v?: string
  voltage_max_v?: string
  current_a?: string
  power_factor?: string
  frequency_hz?: string
  tou_period?: string
  rate_per_kwh?: string
  energy_cost?: string
  rate_plan_name?: string
  rate_version_id?: string
  rate_effective_from?: string
  mixed_rates: boolean
  coverage_percent: string
  missing_sensor_ids: string[]
  quality_flags: string[]
  rate_contributions: HistoryRateContribution[]
}

export interface HistorySummary {
  start_utc: string
  end_utc: string
  energy_kwh?: string
  energy_cost?: string
  blended_rate_per_kwh?: string
  average_power_w?: string
  peak_power_w?: string
  highest_cost_bucket_start?: string
  highest_cost_bucket_value?: string
  highest_usage_bucket_start?: string
  highest_usage_bucket_kwh?: string
  coverage_percent: string
  contributing_sensor_count: number
  tou_breakdown: Record<string, { energy_kwh: string; energy_cost: string }>
}

export interface HistoryQueryResponse {
  scope: {
    type: HistoryScopeType
    display_name: string
    site_id: string
    site_name: string
    timezone: string
    included_device_ids: string[]
    included_device_names: string[]
    excluded_device_ids: string[]
    mixed_rates: boolean
  }
  display_mode: HistoryDisplayMode
  metrics: HistoryMetric[]
  bucket: string
  summary: HistorySummary
  selected_summary?: HistorySummary
  combined: HistoryBucket[]
  individual: Array<{ device_id: string; name: string; circuit_name?: string; status: string; points: HistoryBucket[] }>
  rate_versions_used: Array<{ rate_plan_id: string; rate_plan_name: string; rate_version_id: string; rate_version: number; effective_from: string }>
  warnings: Array<{ code: string; message: string; device_ids?: string[] }>
  total_buckets: number
  page: number
  page_size: number
  next_page?: number
}

export interface PermissionDefinition {
  code: string
  group: string
  label: string
  description: string
  high_risk: boolean
}

export interface AccessRole {
  id: string
  display_name: string
  description: string
  built_in: boolean
  archived: boolean
  revision: number
  permissions: string[]
  assigned_user_count: number
  created_at: string
  updated_at: string
}

export interface ManagedUser extends User {
  is_active: boolean
  status: 'active' | 'disabled'
  all_sites: boolean
  sites: Array<{ id: string; name: string }>
  site_ids: string[]
  permissions: string[]
  permission_count: number
  mfa_enabled: boolean
  last_login_at?: string
  active_session_count: number
  created_at: string
  access_revision: number
  protected_administrator: boolean
  sessions?: Array<{
    id: string
    created_at: string
    last_seen_at: string
    expires_at: string
    source_ip?: string
    user_agent?: string
  }>
  permission_sources?: Record<string, string[]>
}

export interface InterfaceTextDefinition {
  key: string
  section: string
  default: string
  label: string
  description: string
  field_type: 'text' | 'textarea' | 'url'
  required: boolean
  visibility: 'public' | 'authenticated'
  max_length: number
  min_length: number
  line_breaks: boolean
  url_companion: boolean
  markdown: boolean
  blank_allowed: boolean
  preview_location: string
  current_override?: string
  current_value: string
  published_revision: number
}

export interface InterfaceTextRevisionSummary {
  id: string
  revision: number
  created_by: string
  created_at: string
  reason?: string
  restored_from_id?: string
  changed_key_count: number
}

export type StatusBreakpoint = 'desktop' | 'tablet' | 'mobile'
export type StatusDensity = 'compact' | 'standard' | 'detailed'

export interface StatusIndicatorDefinition {
  key: string
  default_label: string
  description: string
  category: string
  data_source: string
  current_value_schema: Record<string, string>
  severity_capability: string[]
  default_enabled: boolean
  default_zone: string
  allowed_zones: string[]
  default_order: number
  supported_pages: string[]
  global_shell_support: boolean
  minimum_display_width: number
  preferred_display_width: number
  presentations: StatusDensity[]
  icon_supported: boolean
  label_supported: boolean
  value_supported: boolean
  freshness_supported: boolean
  role_visibility_supported: boolean
  permission_required: string
  configurable: boolean
  critical_fallback?: string
  renderer: string
  icon: string
  registry_version: string
  metric_identity: string
  canonical_priority: number
  allow_duplicate: boolean
  suppress_when_empty: boolean
  hide_in_zero_data_state: boolean
  diagnostics_only: boolean
}

export interface StatusLayoutItem {
  indicator_key: string
  page: string
  role: string
  breakpoint: 'default' | StatusBreakpoint
  visible?: boolean
  zone?: string
  order?: number
  density?: StatusDensity
  show_icon?: boolean
  show_label?: boolean
  show_value?: boolean
  show_freshness?: boolean
  show_severity?: boolean
  show_tooltip?: boolean
  definition?: StatusIndicatorDefinition
}

export interface StatusLayoutConfiguration {
  schema_version: 'power-monitor-status-layout/1.0'
  registry_version: string
  personalization_enabled: false
  items: StatusLayoutItem[]
}

export interface StatusRegistryResponse {
  registry_version: string
  indicators: StatusIndicatorDefinition[]
  zones: string[]
  pages: string[]
  breakpoints: StatusBreakpoint[]
}

export interface StatusResolvedLayout {
  schema_version: string
  registry_version: string
  published_revision: number
  page: string
  roles: string[]
  breakpoint: StatusBreakpoint
  zones: Array<{ key: string; items: StatusLayoutItem[] }>
  warnings: Array<{ code: string; indicator_key?: string; metric_identity?: string; canonical_surface?: string; kept_indicator_key?: string; message: string }>
  personalization_enabled: false
}

export interface StatusIndicatorValue {
  status: string
  severity: 'info' | 'success' | 'warning' | 'critical' | 'unknown'
  display_value: string
  detail?: string
  freshness_at?: string
}

export interface StatusValuesResponse {
  registry_version: string
  generated_at: string
  values: Record<string, StatusIndicatorValue>
}

export interface StatusAdminCatalog extends StatusRegistryResponse {
  schema_version: string
  published_revision: number
  roles: Array<{ id: string; label: string }>
  new_indicator_keys: string[]
  excluded_status_surfaces: Array<{ surface: string; reason: string }>
}

export interface StatusLayoutDraftResponse {
  exists: boolean
  base_revision: number
  draft_revision: number
  previewed_revision?: number
  configuration: StatusLayoutConfiguration
  edited_by?: string
  reason?: string
  updated_at?: string
  critical_hidden: Array<{ indicator_key: string; fallback: string }>
}

export interface StatusLayoutRevisionSummary {
  id: string
  revision: number
  registry_version: string
  created_by?: string
  created_at: string
  reason?: string
  restored_from_id?: string
}

export interface StatusPreviewResponse {
  layout: StatusResolvedLayout
  values: Record<string, StatusIndicatorValue>
  warnings: Array<{ code: string; indicator_key?: string; message: string }>
}
