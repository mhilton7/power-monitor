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
  recent_peak_w: string
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
