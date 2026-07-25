import type { RatePlanDocument } from './rates'

export const billImportEditableFields = [
  ['plan_name', 'Plan name'],
  ['plan_code', 'Plan code'],
  ['utility', 'Utility'],
  ['description', 'Description'],
  ['currency', 'Currency'],
  ['timezone', 'Timezone'],
  ['effective_from', 'Effective date'],
  ['effective_through', 'Optional end date'],
] as const

export type BillImportDraftChoice = 'import' | 'keep' | 'manual'
export type BillImportDraftChoices = Record<string, BillImportDraftChoice>

export interface BillImportMergeResult {
  document: RatePlanDocument
  appliedGroups: string[]
}

const hasReviewedValue = (value: unknown): boolean => {
  if (value === null || value === undefined) return false
  if (typeof value === 'string') return value.trim().length > 0
  return true
}

const copyTariffRules = (
  target: RatePlanDocument,
  imported: RatePlanDocument,
): void => {
  target.pricing_model = imported.pricing_model
  target.flat_rate_per_kwh = imported.flat_rate_per_kwh
  target.billing_cycle = structuredClone(imported.billing_cycle)
  target.tiers = structuredClone(imported.tiers)
  target.hybrid_pricing = structuredClone(imported.hybrid_pricing)
  target.seasons = structuredClone(imported.seasons)
  target.adjustments = structuredClone(imported.adjustments)
  target.provider_mode = imported.provider_mode
  target.cost_scope_default = imported.cost_scope_default
}

const copyEvidence = (
  target: RatePlanDocument,
  imported: RatePlanDocument,
  options: { nonblankOnly: boolean },
): boolean => {
  let copied = false
  for (const key of [
    'source_label',
    'source_note',
    'cloned_from_rate_version_id',
  ] as const) {
    if (options.nonblankOnly && !hasReviewedValue(imported[key])) continue
    target[key] = imported[key] as never
    copied = true
  }
  return copied
}

export function mergeAllReviewedBillValues(
  current: RatePlanDocument,
  imported: RatePlanDocument,
): BillImportMergeResult {
  const next = structuredClone(current)
  const appliedGroups: string[] = []
  for (const [key, label] of billImportEditableFields) {
    if (!hasReviewedValue(imported[key])) continue
    next[key] = imported[key] as never
    appliedGroups.push(label)
  }
  copyTariffRules(next, imported)
  appliedGroups.push('Complete tariff rules')
  if (copyEvidence(next, imported, { nonblankOnly: true })) {
    appliedGroups.push('Source evidence references')
  }
  return { document: next, appliedGroups }
}

export function mergeSelectedReviewedBillValues(
  current: RatePlanDocument,
  imported: RatePlanDocument,
  choices: BillImportDraftChoices,
  manualValues: Record<string, string>,
): BillImportMergeResult {
  const next = structuredClone(current)
  const appliedGroups: string[] = []
  for (const [key, label] of billImportEditableFields) {
    const choice = choices[key] ?? 'keep'
    if (choice === 'import') {
      next[key] = imported[key] as never
      appliedGroups.push(label)
    } else if (choice === 'manual') {
      next[key] = (key === 'effective_through'
        ? manualValues[key] || null
        : manualValues[key] ?? '') as never
      appliedGroups.push(label)
    }
  }
  if ((choices.tariff_rules ?? 'keep') === 'import') {
    copyTariffRules(next, imported)
    appliedGroups.push('Complete tariff rules')
  }
  if ((choices.source_evidence ?? 'keep') === 'import') {
    copyEvidence(next, imported, { nonblankOnly: false })
    appliedGroups.push('Source evidence references')
  }
  return { document: next, appliedGroups }
}

export const hasSelectedBillImportValues = (
  choices: BillImportDraftChoices,
): boolean => Object.values(choices).some((choice) => choice !== 'keep')
