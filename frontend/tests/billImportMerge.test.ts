import { describe, expect, it } from 'vitest'
import {
  hasSelectedBillImportValues,
  mergeAllReviewedBillValues,
  mergeSelectedReviewedBillValues,
} from '../src/billImportMerge'
import { emptyRateDocument } from '../src/rates'

const reviewedDocument = () => ({
  ...emptyRateDocument(),
  plan_name: 'SCE DOMESTIC',
  plan_code: 'D',
  description: '',
  effective_from: '2026-07-22',
  effective_through: null,
  pricing_model: 'tiered' as const,
  tiers: [{
    tier_id: 'tier-1',
    name: 'Tier 1',
    order: 0,
    lower_bound_inclusive_kwh: '0',
    upper_bound_exclusive_kwh: null,
    lower_bound_multiplier: null,
    upper_bound_multiplier: null,
    price_per_kwh: '0.30863000',
    tou_prices: {},
    season: null,
    source_citation: 'utility-bill:reviewed',
  }],
  seasons: [],
  source_label: 'Reviewed utility bill',
  source_note: 'Sanitized source evidence',
})

describe('utility-bill Custom Plan merges', () => {
  it('applies every nonblank reviewed field, validated tariff rules, and source evidence', () => {
    const current = {
      ...emptyRateDocument(),
      plan_name: 'Temporary plan',
      description: 'Keep this description',
      effective_through: '2027-01-01',
      custom_notes: 'Administrator note',
    }
    const result = mergeAllReviewedBillValues(current, reviewedDocument())

    expect(result.document.plan_name).toBe('SCE DOMESTIC')
    expect(result.document.plan_code).toBe('D')
    expect(result.document.description).toBe('Keep this description')
    expect(result.document.effective_through).toBe('2027-01-01')
    expect(result.document.pricing_model).toBe('tiered')
    expect(result.document.tiers).toHaveLength(1)
    expect(result.document.source_label).toBe('Reviewed utility bill')
    expect(result.document.custom_notes).toBe('Administrator note')
    expect(result.appliedGroups).toContain('Complete tariff rules')
    expect(result.appliedGroups).toContain('Source evidence references')
    expect(result.appliedGroups).not.toContain('Description')
    expect(result.appliedGroups).not.toContain('Optional end date')
  })

  it('does not claim that an advanced merge applied values when every choice is keep', () => {
    const current = emptyRateDocument()
    const result = mergeSelectedReviewedBillValues(current, reviewedDocument(), {}, {})

    expect(hasSelectedBillImportValues({})).toBe(false)
    expect(result.appliedGroups).toEqual([])
    expect(result.document).toEqual(current)
  })

  it('keeps the advanced path selective and supports explicit manual values', () => {
    const result = mergeSelectedReviewedBillValues(
      emptyRateDocument(),
      reviewedDocument(),
      {
        plan_name: 'manual',
        plan_code: 'import',
        tariff_rules: 'import',
        source_evidence: 'keep',
      },
      { plan_name: 'My custom name' },
    )

    expect(hasSelectedBillImportValues({
      plan_name: 'manual',
      plan_code: 'import',
    })).toBe(true)
    expect(result.document.plan_name).toBe('My custom name')
    expect(result.document.plan_code).toBe('D')
    expect(result.document.pricing_model).toBe('tiered')
    expect(result.document.source_label).toBe('Administrator-defined rate plan')
    expect(result.appliedGroups).toEqual([
      'Plan name',
      'Plan code',
      'Complete tariff rules',
    ])
  })
})
