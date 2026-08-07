export type PricingModel = 'flat' | 'time_of_use' | 'tiered' | 'time_of_use_tiered'

export interface RatePeriodDraft {
  label: string
  start_minute: number
  end_minute: number
  price_per_kwh: string
  delivery_per_kwh: string
  generation_per_kwh: string
  adjustment_per_kwh: string
  display_order: number
}

export interface RateScheduleDraft {
  day_type: 'weekday' | 'weekend' | 'all-days' | 'holiday' | 'date-override'
  dates: string[]
  periods: RatePeriodDraft[]
}

export interface RateSeasonDraft {
  name: string
  start: string
  end: string
  priority: number
  leap_day_behavior: 'include' | 'previous_day' | 'next_day'
  schedules: RateScheduleDraft[]
}

export interface RateTierDraft {
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

export interface RateAdjustmentDraft {
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

export interface RatePlanDraft {
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
    threshold: {
      basis: 'fixed_cycle_kwh' | 'daily_baseline_kwh'
      daily_baseline_kwh: string | null
      baseline_region: string | null
      baseline_category: string | null
      rounding_policy: 'none' | 'nearest_kwh' | 'floor_kwh' | 'ceil_kwh'
      seasonal_baselines: Array<{
        name: string
        start: string
        end: string
        daily_kwh: string
        source_citation: string | null
      }>
      source_citation: string | null
    }
  }
  tiers: RateTierDraft[]
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
  seasons: RateSeasonDraft[]
  adjustments: RateAdjustmentDraft[]
  custom_notes: string
  cloned_from_rate_version_id: string | null
}

export interface RateValidationResult {
  valid: boolean
  errors: Array<{ level: string; code: string; path: string; message: string }>
  warnings: Array<{ level: string; code: string; path: string; message: string }>
  integrity_sha256: string
}

export function newRateDraft(home: { id: string; currency: string; timezone: string }): RatePlanDraft {
  return {
    schema_version: 'power-monitor-rate-plan/1.0',
    plan_name: 'My electric plan',
    plan_code: 'CUSTOM-HOME',
    utility: 'Southern California Edison',
    description: '',
    currency: home.currency,
    timezone: home.timezone,
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
    ownership_scope: 'site',
    owner_id: home.id,
    effective_from: new Date().toISOString().slice(0, 10),
    effective_through: null,
    cost_scope_default: 'energy_only',
    source_label: 'Administrator-defined rate plan',
    source_note: '',
    provider_mode: 'custom_combined',
    seasons: [{
      name: 'Year round',
      start: '01-01',
      end: '12-31',
      priority: 0,
      leap_day_behavior: 'include',
      schedules: [{
        day_type: 'all-days',
        dates: [],
        periods: [
          period('Off-Peak', 0, 960, '0.25000000', 0),
          period('On-Peak', 960, 1260, '0.45000000', 1),
          period('Off-Peak', 1260, 1440, '0.25000000', 2),
        ],
      }],
    }],
    adjustments: [],
    custom_notes: '',
    cloned_from_rate_version_id: null,
  }
}

export function period(label: string, start: number, end: number, price: string, order: number): RatePeriodDraft {
  return {
    label,
    start_minute: start,
    end_minute: end,
    price_per_kwh: price,
    delivery_per_kwh: '0',
    generation_per_kwh: '0',
    adjustment_per_kwh: '0',
    display_order: order,
  }
}

export function tier(order: number, lower: string, upper: string | null): RateTierDraft {
  return {
    tier_id: `tier-${order + 1}`,
    name: `Tier ${order + 1}`,
    order,
    lower_bound_inclusive_kwh: lower,
    upper_bound_exclusive_kwh: upper,
    lower_bound_multiplier: order === 0 ? '0' : null,
    upper_bound_multiplier: null,
    price_per_kwh: '0.25000000',
    tou_prices: {},
    season: null,
    source_citation: null,
  }
}

export function adjustment(order: number): RateAdjustmentDraft {
  return {
    name: 'New adjustment',
    component: 'other',
    operation: 'add',
    value: '0.00000000',
    unit: 'per_kwh',
    scope: 'full_account_estimate',
    eligibility: {},
    effective_from: null,
    effective_to: null,
    calculation_order: order,
    description: '',
  }
}

export function adaptRatePlanDraft(value: unknown): RatePlanDraft {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('The rate version response is invalid.')
  const root = value as Record<string, unknown>
  const candidate = root.document && typeof root.document === 'object' && !Array.isArray(root.document)
    ? root.document as Record<string, unknown>
    : root
  if (
    candidate.schema_version !== 'power-monitor-rate-plan/1.0'
    || typeof candidate.plan_name !== 'string'
    || typeof candidate.plan_code !== 'string'
    || !Array.isArray(candidate.seasons)
    || !Array.isArray(candidate.tiers)
    || !Array.isArray(candidate.adjustments)
  ) throw new Error('The server returned an incomplete rate-plan document.')
  return structuredClone(candidate) as unknown as RatePlanDraft
}
