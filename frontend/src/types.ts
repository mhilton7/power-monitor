export type Role = 'admin' | 'operator' | 'viewer'

export interface User {
  id: string
  email: string
  display_name: string
  roles: Role[]
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
