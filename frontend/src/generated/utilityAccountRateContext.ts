// Generated from shared/schemas/utility-account-rate-context-1.0.json.
// Regenerate with scripts/generate_frontend_contracts.py; do not edit by hand.

export const UTILITY_ACCOUNT_RATE_CONTEXT_SCHEMA_VERSION =
  'utility-account-rate-context/1.0' as const

export interface BillImportAccountSummary {
  id: string
  site_id: string
  site_name: string
  name: string
  utility_name: string
  timezone: string
  currency: string
  provider_mode: string
}

export interface BillImportRatePlanSummary {
  id: string
  code: string
  name: string
}

export interface BillImportRateAssignmentSummary {
  id: string
  rate_version_id: string
  effective_from: string
  effective_to: string | null
}

export interface BillImportRateVersionSummary {
  id: string
  version: number
  pricing_model: string
  effective_from: string
  effective_to: string | null
  status: string
}

export interface BillImportRatePeriodSummary {
  label: string
  price_per_kwh: string | number | null
  currency: string
}

export interface BillImportRateReadiness {
  account_configured: boolean
  rate_assigned: boolean
  rate_effective: boolean
}

export interface UtilityAccountRateContext {
  schema_version: typeof UTILITY_ACCOUNT_RATE_CONTEXT_SCHEMA_VERSION
  api_version: string
  backend_version: string
  backend_commit: string | null
  generated_client_schema_version: typeof UTILITY_ACCOUNT_RATE_CONTEXT_SCHEMA_VERSION
  account_id: string | null
  site_id: string | null
  account: BillImportAccountSummary | null
  available_accounts: BillImportAccountSummary[]
  current_plan: BillImportRatePlanSummary | null
  current_assignment: BillImportRateAssignmentSummary | null
  current_rate_version: BillImportRateVersionSummary | null
  current_period: BillImportRatePeriodSummary | null
  readiness: BillImportRateReadiness
}
