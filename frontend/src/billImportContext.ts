import type { RatePlanDocument } from './rates'
import { emptyRateDocument } from './rates'
import {
  UTILITY_ACCOUNT_RATE_CONTEXT_SCHEMA_VERSION,
  type BillImportAccountSummary,
  type BillImportRateAssignmentSummary,
  type BillImportRatePlanSummary,
  type BillImportRateReadiness,
  type UtilityAccountRateContext,
} from './generated/utilityAccountRateContext'

export interface AppError {
  code: string
  title: string
  message: string
  retryable: boolean
  correlation_id?: string
  technical_details?: string
}

export class AppContractError extends Error implements AppError {
  readonly code = 'bill_import_context_invalid'
  readonly title = 'Utility-account context is incompatible'
  readonly message =
    'The server returned utility-account data this dashboard cannot safely use.'
  readonly retryable = true
  readonly correlation_id: string
  readonly technical_details: string

  constructor(detail: string) {
    super(detail)
    this.name = 'AppContractError'
    this.correlation_id = createCorrelationId()
    this.technical_details = detail
  }
}

export type ImporterMode =
  | 'new_custom_plan'
  | 'existing_custom_plan_draft'
  | 'clone_existing_plan'
  | 'account_without_plan'
  | 'account_with_plan'
  | 'no_account_selected'
  | 'legacy_redirect'

export type BillImportState<Extraction> =
  | { status: 'initializing'; draft: RatePlanDocument }
  | { status: 'ready_for_upload'; draft: RatePlanDocument; mode: ImporterMode }
  | { status: 'uploading'; draft: RatePlanDocument; progress: number }
  | { status: 'extracting'; draft: RatePlanDocument; job_id: string }
  | { status: 'review'; draft: RatePlanDocument; extraction: Extraction }
  | { status: 'applying'; draft: RatePlanDocument; extraction: Extraction }
  | { status: 'complete'; draft: RatePlanDocument; extraction: Extraction }
  | { status: 'recoverable_error'; draft: RatePlanDocument; error: AppError }
  | { status: 'fatal_error'; draft: RatePlanDocument; error: AppError }

const objectValue = (value: unknown, path: string): Record<string, unknown> => {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new AppContractError(`${path} must be an object`)
  }
  return value as Record<string, unknown>
}

const stringValue = (value: unknown, path: string): string => {
  if (typeof value !== 'string' || value.length === 0) {
    throw new AppContractError(`${path} must be a non-empty string`)
  }
  return value
}

const nullableString = (value: unknown, path: string): string | null => {
  if (value === null) return null
  return stringValue(value, path)
}

const booleanValue = (value: unknown, path: string): boolean => {
  if (typeof value !== 'boolean') throw new AppContractError(`${path} must be a boolean`)
  return value
}

const accountSummary = (value: unknown, path: string): BillImportAccountSummary => {
  const item = objectValue(value, path)
  return {
    id: stringValue(item.id, `${path}.id`),
    site_id: stringValue(item.site_id, `${path}.site_id`),
    site_name: stringValue(item.site_name, `${path}.site_name`),
    name: stringValue(item.name, `${path}.name`),
    utility_name: stringValue(item.utility_name, `${path}.utility_name`),
    timezone: stringValue(item.timezone, `${path}.timezone`),
    currency: stringValue(item.currency, `${path}.currency`),
    provider_mode: stringValue(item.provider_mode, `${path}.provider_mode`),
  }
}

const nullablePlan = (value: unknown): UtilityAccountRateContext['current_plan'] => {
  if (value === null) return null
  const item = objectValue(value, 'current_plan')
  return {
    id: stringValue(item.id, 'current_plan.id'),
    code: stringValue(item.code, 'current_plan.code'),
    name: stringValue(item.name, 'current_plan.name'),
  }
}

const nullableAssignment = (
  value: unknown,
): UtilityAccountRateContext['current_assignment'] => {
  if (value === null) return null
  const item = objectValue(value, 'current_assignment')
  return {
    id: stringValue(item.id, 'current_assignment.id'),
    rate_version_id: stringValue(
      item.rate_version_id,
      'current_assignment.rate_version_id',
    ),
    effective_from: stringValue(item.effective_from, 'current_assignment.effective_from'),
    effective_to: nullableString(item.effective_to, 'current_assignment.effective_to'),
  }
}

const nullableVersion = (
  value: unknown,
): UtilityAccountRateContext['current_rate_version'] => {
  if (value === null) return null
  const item = objectValue(value, 'current_rate_version')
  if (!Number.isInteger(item.version) || Number(item.version) < 1) {
    throw new AppContractError('current_rate_version.version must be a positive integer')
  }
  return {
    id: stringValue(item.id, 'current_rate_version.id'),
    version: Number(item.version),
    pricing_model: stringValue(item.pricing_model, 'current_rate_version.pricing_model'),
    effective_from: stringValue(item.effective_from, 'current_rate_version.effective_from'),
    effective_to: nullableString(item.effective_to, 'current_rate_version.effective_to'),
    status: stringValue(item.status, 'current_rate_version.status'),
  }
}

const nullablePeriod = (value: unknown): UtilityAccountRateContext['current_period'] => {
  if (value === null) return null
  const item = objectValue(value, 'current_period')
  if (
    item.price_per_kwh !== null &&
    typeof item.price_per_kwh !== 'string' &&
    typeof item.price_per_kwh !== 'number'
  ) {
    throw new AppContractError(
      'current_period.price_per_kwh must be a string, number, or null',
    )
  }
  return {
    label: stringValue(item.label, 'current_period.label'),
    price_per_kwh: item.price_per_kwh,
    currency: stringValue(item.currency, 'current_period.currency'),
  }
}

export function parseUtilityAccountRateContext(value: unknown): UtilityAccountRateContext {
  const item = objectValue(value, 'utility_account_rate_context')
  if (item.schema_version !== UTILITY_ACCOUNT_RATE_CONTEXT_SCHEMA_VERSION) {
    throw new AppContractError(
      `Expected schema ${UTILITY_ACCOUNT_RATE_CONTEXT_SCHEMA_VERSION}; received ${String(item.schema_version)}`,
    )
  }
  if (item.generated_client_schema_version !== UTILITY_ACCOUNT_RATE_CONTEXT_SCHEMA_VERSION) {
    throw new AppContractError(
      `Server generated-client schema ${String(item.generated_client_schema_version)} is incompatible`,
    )
  }
  if (!Array.isArray(item.available_accounts)) {
    throw new AppContractError('available_accounts must be an array')
  }
  const readiness = objectValue(item.readiness, 'readiness')
  const account = item.account === null ? null : accountSummary(item.account, 'account')
  const result: UtilityAccountRateContext = {
    schema_version: UTILITY_ACCOUNT_RATE_CONTEXT_SCHEMA_VERSION,
    api_version: stringValue(item.api_version, 'api_version'),
    backend_version: stringValue(item.backend_version, 'backend_version'),
    backend_commit: nullableString(item.backend_commit, 'backend_commit'),
    generated_client_schema_version: UTILITY_ACCOUNT_RATE_CONTEXT_SCHEMA_VERSION,
    account_id: nullableString(item.account_id, 'account_id'),
    site_id: nullableString(item.site_id, 'site_id'),
    account,
    available_accounts: item.available_accounts.map((candidate, index) =>
      accountSummary(candidate, `available_accounts[${index}]`),
    ),
    current_plan: nullablePlan(item.current_plan),
    current_assignment: nullableAssignment(item.current_assignment),
    current_rate_version: nullableVersion(item.current_rate_version),
    current_period: nullablePeriod(item.current_period),
    readiness: {
      account_configured: booleanValue(
        readiness.account_configured,
        'readiness.account_configured',
      ),
      rate_assigned: booleanValue(readiness.rate_assigned, 'readiness.rate_assigned'),
      rate_effective: booleanValue(readiness.rate_effective, 'readiness.rate_effective'),
    },
  }
  if ((result.account_id === null) !== (result.account === null)) {
    throw new AppContractError('account and account_id must be null together')
  }
  if (result.account !== null && result.account.id !== result.account_id) {
    throw new AppContractError('account.id does not match account_id')
  }
  if (result.readiness.rate_assigned && result.current_rate_version === null) {
    throw new AppContractError('rate_assigned requires current_rate_version')
  }
  return result
}

export function getCurrentPlan(
  context: UtilityAccountRateContext | null | undefined,
): BillImportRatePlanSummary | null {
  return context?.current_plan ?? null
}

export function getCurrentAssignment(
  context: UtilityAccountRateContext | null | undefined,
): BillImportRateAssignmentSummary | null {
  return context?.current_assignment ?? null
}

export function getRateContextReadiness(
  context: UtilityAccountRateContext | null | undefined,
): BillImportRateReadiness {
  return (
    context?.readiness ?? {
      account_configured: false,
      rate_assigned: false,
      rate_effective: false,
    }
  )
}

export function resolveImporterMode(input: {
  accountId: string | null
  currentPlan: BillImportRatePlanSummary | null
  existingDraft: boolean
  clonedFromVersionId: string | null
  legacyRedirect: boolean
  newCustomPlan: boolean
}): ImporterMode {
  if (input.legacyRedirect) return 'legacy_redirect'
  if (input.existingDraft) return 'existing_custom_plan_draft'
  if (input.clonedFromVersionId) return 'clone_existing_plan'
  if (!input.accountId) return 'no_account_selected'
  if (input.newCustomPlan) return 'new_custom_plan'
  return input.currentPlan ? 'account_with_plan' : 'account_without_plan'
}

export function createEmptyCustomPlanDraft(): RatePlanDocument {
  return emptyRateDocument()
}

export function createCorrelationId(): string {
  return globalThis.crypto.randomUUID()
}

export function toAppError(error: unknown): AppError {
  if (error instanceof AppContractError) return error
  const candidate =
    error !== null && typeof error === 'object'
      ? (error as {
          problem?: {
            code?: unknown
            title?: unknown
            detail?: unknown
            request_id?: unknown
            status?: unknown
          }
        })
      : {}
  const problem = candidate.problem
  if (problem) {
    return {
      code: typeof problem.code === 'string' ? problem.code : 'request_failed',
      title: typeof problem.title === 'string' ? problem.title : 'Request failed',
      message:
        typeof problem.detail === 'string'
          ? problem.detail
          : 'The server could not complete the request.',
      retryable:
        typeof problem.status !== 'number' ||
        problem.status === 408 ||
        problem.status === 409 ||
        problem.status === 429 ||
        problem.status >= 500,
      correlation_id:
        typeof problem.request_id === 'string' ? problem.request_id : createCorrelationId(),
    }
  }
  return {
    code: 'unexpected_error',
    title: 'This part of the dashboard could not be opened',
    message: 'Retry the operation. Your unsaved Custom Plan draft has been preserved.',
    retryable: true,
    correlation_id: createCorrelationId(),
    technical_details: error instanceof Error ? error.message : String(error),
  }
}
