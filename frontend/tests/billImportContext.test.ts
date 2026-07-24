import { describe, expect, it } from 'vitest'
import {
  AppContractError,
  createEmptyCustomPlanDraft,
  getCurrentAssignment,
  getCurrentPlan,
  getRateContextReadiness,
  parseUtilityAccountRateContext,
  resolveImporterMode,
} from '../src/billImportContext'

const baseContext = {
  schema_version: 'utility-account-rate-context/1.0',
  api_version: '1.0.0',
  backend_version: '1.0.0',
  backend_commit: null,
  generated_client_schema_version: 'utility-account-rate-context/1.0',
  account_id: null,
  site_id: null,
  account: null,
  available_accounts: [],
  current_plan: null,
  current_assignment: null,
  current_rate_version: null,
  current_period: null,
  readiness: {
    account_configured: false,
    rate_assigned: false,
    rate_effective: false,
  },
}

describe('utility-account bill-import context contract', () => {
  it('accepts an explicit-null no-account response', () => {
    const context = parseUtilityAccountRateContext(baseContext)
    expect(getCurrentPlan(context)).toBeNull()
    expect(getCurrentAssignment(context)).toBeNull()
    expect(getRateContextReadiness(context)).toEqual({
      account_configured: false,
      rate_assigned: false,
      rate_effective: false,
    })
  })

  it('accepts an account without a current plan', () => {
    const account = {
      id: 'account-1',
      site_id: 'site-1',
      site_name: 'Upland Site',
      name: 'Home',
      utility_name: 'Southern California Edison',
      timezone: 'America/Los_Angeles',
      currency: 'USD',
      provider_mode: 'sce_bundled',
    }
    const context = parseUtilityAccountRateContext({
      ...baseContext,
      account_id: account.id,
      site_id: account.site_id,
      account,
      available_accounts: [account],
      readiness: {
        account_configured: true,
        rate_assigned: false,
        rate_effective: false,
      },
    })
    expect(context.account?.name).toBe('Home')
    expect(context.current_plan).toBeNull()
  })

  it('accepts an account with a plan and missing current period', () => {
    const account = {
      id: 'account-1',
      site_id: 'site-1',
      site_name: 'Upland Site',
      name: 'Home',
      utility_name: 'Southern California Edison',
      timezone: 'America/Los_Angeles',
      currency: 'USD',
      provider_mode: 'sce_bundled',
    }
    const context = parseUtilityAccountRateContext({
      ...baseContext,
      account_id: account.id,
      site_id: account.site_id,
      account,
      available_accounts: [account],
      current_plan: { id: 'plan-1', code: 'TOU-D', name: 'TOU-D' },
      current_assignment: {
        id: 'assignment-1',
        rate_version_id: 'version-1',
        effective_from: '2026-07-01T00:00:00Z',
        effective_to: null,
      },
      current_rate_version: {
        id: 'version-1',
        version: 2,
        pricing_model: 'time_of_use',
        effective_from: '2026-07-01',
        effective_to: null,
        status: 'active',
      },
      current_period: null,
      readiness: {
        account_configured: true,
        rate_assigned: true,
        rate_effective: true,
      },
    })
    expect(getCurrentPlan(context)?.name).toBe('TOU-D')
    expect(context.current_period).toBeNull()
  })

  it.each([
    ['no_account_selected', { accountId: null, currentPlan: null, existingDraft: false, clonedFromVersionId: null, legacyRedirect: false, newCustomPlan: true }],
    ['new_custom_plan', { accountId: 'account-1', currentPlan: null, existingDraft: false, clonedFromVersionId: null, legacyRedirect: false, newCustomPlan: true }],
    ['existing_custom_plan_draft', { accountId: null, currentPlan: null, existingDraft: true, clonedFromVersionId: null, legacyRedirect: false, newCustomPlan: false }],
    ['clone_existing_plan', { accountId: null, currentPlan: null, existingDraft: false, clonedFromVersionId: 'version-1', legacyRedirect: false, newCustomPlan: false }],
    ['account_without_plan', { accountId: 'account-1', currentPlan: null, existingDraft: false, clonedFromVersionId: null, legacyRedirect: false, newCustomPlan: false }],
    ['account_with_plan', { accountId: 'account-1', currentPlan: { id: 'plan-1', code: 'TOU-D', name: 'TOU-D' }, existingDraft: false, clonedFromVersionId: null, legacyRedirect: false, newCustomPlan: false }],
    ['legacy_redirect', { accountId: null, currentPlan: null, existingDraft: false, clonedFromVersionId: null, legacyRedirect: true, newCustomPlan: true }],
  ] as const)('resolves %s initialization', (expected, input) => {
    expect(resolveImporterMode(input)).toBe(expected)
  })

  it('creates an independent valid empty Custom Plan draft', () => {
    const first = createEmptyCustomPlanDraft()
    const second = createEmptyCustomPlanDraft()
    first.plan_name = 'Changed'
    expect(second.plan_name).not.toBe('Changed')
  })

  it.each([
    { ...baseContext, current_plan: undefined },
    { ...baseContext, readiness: undefined },
    { ...baseContext, schema_version: 'utility-account-rate-context/2.0' },
    { ...baseContext, account_id: 'account-1', account: null },
  ])('rejects malformed context without dereferencing undefined (case %#)', (value) => {
    expect(() => parseUtilityAccountRateContext(value)).toThrow(AppContractError)
  })
})
