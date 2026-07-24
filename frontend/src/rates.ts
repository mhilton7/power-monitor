export type IndexedArray<T> = Omit<T[], number> & Record<number, T>

export interface RatePeriodDocument {
  label: string
  start_minute: number
  end_minute: number
  price_per_kwh: string
  delivery_per_kwh: string
  generation_per_kwh: string
  adjustment_per_kwh: string
  display_order: number
}

export interface DayScheduleDocument {
  day_type: 'weekday' | 'weekend' | 'all-days' | 'holiday' | 'date-override'
  dates: string[]
  periods: IndexedArray<RatePeriodDocument>
}

export interface RateSeasonDocument {
  name: string
  start: string
  end: string
  priority: number
  leap_day_behavior: 'include' | 'previous_day' | 'next_day'
  schedules: IndexedArray<DayScheduleDocument>
}

export interface RateAdjustmentDocument {
  name: string
  component: 'daily_fixed_charge' | 'monthly_fixed_charge' | 'minimum_charge' | 'baseline_credit' | 'percentage_tax' | 'fixed_tax' | 'generation_provider' | 'cca' | 'direct_access' | 'manual_credit' | 'other'
  operation: 'add' | 'subtract' | 'minimum' | 'multiply'
  value: string
  unit: string
  scope: string
  eligibility: Record<string, unknown>
  effective_from: string | null
  effective_to: string | null
  calculation_order: number
  description: string
}

export type PricingModel = 'flat' | 'time_of_use' | 'tiered' | 'time_of_use_tiered'

export interface SeasonalBaselineDocument {
  name: string
  start: string
  end: string
  daily_kwh: string
  source_citation: string | null
}

export interface TierThresholdDocument {
  basis: 'fixed_cycle_kwh' | 'daily_baseline_kwh'
  daily_baseline_kwh: string | null
  baseline_region: string | null
  baseline_category: string | null
  rounding_policy: 'none' | 'nearest_kwh' | 'floor_kwh' | 'ceil_kwh'
  seasonal_baselines: IndexedArray<SeasonalBaselineDocument>
  source_citation: string | null
}

export interface TierDefinitionDocument {
  tier_id: string
  name: string
  order: number
  lower_bound_inclusive_kwh: string
  upper_bound_exclusive_kwh: string | null
  lower_bound_multiplier: string | null
  upper_bound_multiplier: string | null
  price_per_kwh: string
  tou_prices: Record<string, string>
  season: string | null
  source_citation: string | null
}

export interface RatePlanDocument {
  schema_version: 'power-monitor-rate-plan/1.0'
  plan_name: string
  plan_code: string
  utility: string
  description: string
  currency: string
  timezone: string
  pricing_model: PricingModel
  flat_rate_per_kwh: string | null
  billing_cycle: {
    expected_start_day: number
    threshold: TierThresholdDocument
  }
  tiers: IndexedArray<TierDefinitionDocument>
  hybrid_pricing: {
    method: 'tier_period_matrix' | 'tier_base_plus_tou_adder' | 'tou_base_plus_tier_adder'
  } | null
  ownership_scope: 'global' | 'site' | 'utility_account'
  owner_id: string | null
  effective_from: string
  effective_through: string | null
  cost_scope_default: 'energy_only' | 'allocated_account_estimate' | 'full_account_estimate'
  source_label: string
  source_note: string
  provider_mode: 'sce_delivery_generation' | 'sce_delivery_cca' | 'sce_delivery_direct_access' | 'custom_combined'
  seasons: IndexedArray<RateSeasonDocument>
  adjustments: IndexedArray<RateAdjustmentDocument>
  custom_notes: string
  cloned_from_rate_version_id: string | null
}

export interface ManagedRateVersion {
  id: string
  version: number
  pricing_model: PricingModel
  tier_count: number
  threshold_basis?: 'fixed_cycle_kwh' | 'daily_baseline_kwh'
  effective_from: string
  effective_through?: string | null
  status: string
  source_kind: string
  source_checked_at?: string
  source_label?: string
  integrity_sha256: string
  is_active: boolean
  immutable: boolean
  created_at: string
  approved_at?: string
  activated_at?: string
}

export interface ManagedRatePlan {
  id: string
  code: string
  name: string
  description: string
  plan_kind: string
  ownership_scope: string
  currency: string
  timezone: string
  status: string
  versions: IndexedArray<ManagedRateVersion>
}

export interface ValidationIssue {
  level: 'error' | 'warning'
  code: string
  path: string
  message: string
}

export interface ValidationReport {
  valid: boolean
  errors: ValidationIssue[]
  warnings: ValidationIssue[]
  integrity_sha256: string
  coverage: Record<string, boolean>
}

export const emptyRateDocument = (): RatePlanDocument => ({
  schema_version: 'power-monitor-rate-plan/1.0',
  plan_name: '',
  plan_code: 'CUSTOM-PLAN',
  utility: 'Southern California Edison',
  description: '',
  currency: 'USD',
  timezone: 'America/Los_Angeles',
  pricing_model: 'time_of_use',
  flat_rate_per_kwh: null,
  billing_cycle: {
    expected_start_day: 1,
    threshold: {
      basis: 'fixed_cycle_kwh',
      daily_baseline_kwh: null,
      baseline_region: null,
      baseline_category: null,
      rounding_policy: 'none',
      seasonal_baselines: [],
      source_citation: null,
    },
  },
  tiers: [],
  hybrid_pricing: null,
  ownership_scope: 'global',
  owner_id: null,
  effective_from: new Date().toISOString().slice(0, 10),
  effective_through: null,
  cost_scope_default: 'energy_only',
  source_label: 'Administrator-defined rate plan',
  source_note: '',
  provider_mode: 'custom_combined',
  seasons: [{
    name: 'all-year',
    start: '01-01',
    end: '12-31',
    priority: 0,
    leap_day_behavior: 'include',
    schedules: [{
      day_type: 'all-days',
      dates: [],
      periods: [{ label: 'flat', start_minute: 0, end_minute: 1440, price_per_kwh: '0.25000000', delivery_per_kwh: '0', generation_per_kwh: '0', adjustment_per_kwh: '0', display_order: 0 }],
    }],
  }],
  adjustments: [],
  custom_notes: '',
  cloned_from_rate_version_id: null,
})
