import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Download,
  FileSearch,
  FileUp,
  Save,
  ShieldCheck,
  Trash2,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, apiDownload } from '../api'
import {
  AppContractError,
  getCurrentPlan,
  getRateContextReadiness,
  parseUtilityAccountRateContext,
  resolveImporterMode,
  toAppError,
  type AppError,
  type BillImportState,
} from '../billImportContext'
import { AppErrorBoundary } from '../components/AppErrorBoundary'
import { EmptyState, ErrorState, LoadingState, Panel, StatusPill, formatTime } from '../components/UI'
import {
  formatBillingPeriod,
  formatCurrency,
  formatDecimalDetail,
  formatEnergy,
  formatEnergyRate,
  formatStructuredLabel,
  formatTierRange,
} from '../formatters'
import type { UtilityAccountRateContext } from '../generated/utilityAccountRateContext'
import type { RatePlanDocument } from '../rates'

type RetentionMode = 'retain' | 'retain_until' | 'delete_after_approval'
type SourceRole = 'supporting' | 'authoritative_account_specific' | 'reference_only'
type ThresholdInterpretation =
  | 'fixed_cycle_threshold'
  | 'daily_baseline'
  | 'baseline_multiplier'
  | 'unknown'
type FieldAction = 'review' | 'confirm' | 'correct' | 'reject'

interface BillField {
  id: string
  output_kind: 'account' | 'rate_plan' | 'billing_cycle'
  field_key: string
  raw_value: unknown
  normalized_value: unknown
  corrected_value: unknown
  effective_value: unknown
  page_number: number | null
  text_region: Record<string, unknown> | null
  source_excerpt: string | null
  extraction_method: string
  parser_version: string
  confidence: string
  review_state: string
  warnings: Array<{ code?: string; message?: string }>
  normalization_history: Array<Record<string, unknown>>
}

interface BillConflict {
  id: string
  field_key: string
  extracted_value: unknown
  configured_value: unknown
  comparison_source: string
  status: string
  blocking: boolean
  resolution_note?: string
}

interface CycleDraft {
  id: string
  status: string
  starts_at?: string
  ends_at?: string
  cycle_days?: number
  meter_read_date?: string
  total_usage_kwh?: string
  usage_by_tier: Array<Record<string, string | null>>
  usage_by_tou: Array<Record<string, string | null>>
  meter_records: Array<Record<string, string | null>>
  current_tier?: string
  projected_tier?: string
  energy_subtotal?: string
  full_bill_total?: string
  fixed_charges?: string
  taxes_fees?: string
  credits?: string
  adjustments?: string
  threshold_interpretation: ThresholdInterpretation
  reconciliation_status: string
  billing_cycle_id?: string
  utility_usage_import_id?: string
  revision: number
}

interface BillImport {
  id: string
  job_id: string
  utility_account_id: string | null
  utility_account_name: string
  artifact_id: string
  content_sha256: string
  status: string
  source_role: SourceRole
  extraction_method: string
  parser_version: string
  page_count: number
  retention_mode: RetentionMode
  retain_until?: string
  original_available: boolean
  original_deleted_at?: string
  rate_plan_id: string | null
  rate_version_id: string | null
  revision: number
  blocking_warnings: Array<{ code?: string; message?: string; fields?: string[] }>
  extraction_warnings: Array<{ code?: string; message?: string; page?: number }>
  created_at: string
  updated_at: string
  normalized: {
    account: Record<string, unknown>
    rate_plan: {
      plan_name?: string
      plan_code?: string
      utility?: string
      pricing_model?: string
      effective_from?: string
      currency?: string
      tiers?: Array<{
        name?: string
        lower_bound_kwh?: string
        upper_bound_kwh?: string | null
        usage_kwh?: string
        price_per_kwh?: string
        energy_charge?: string
      }>
      [key: string]: unknown
    }
    billing_cycle: Record<string, unknown>
  }
  fields: BillField[]
  conflicts: BillConflict[]
  cycle_draft?: CycleDraft
}

interface BillSummary {
  id: string
  utility_account_id: string | null
  utility_account_name: string
  status: string
  extraction_method: string
  page_count: number
  retention_mode: string
  original_available: boolean
  revision: number
  blocking_warnings: BillImport['blocking_warnings']
  created_at: string
}

interface BillComparison {
  available: boolean
  reason?: string
  calculation_correctness?: string
  extraction_confidence?: string
  exact?: {
    usage_kwh: string
    calculated_energy_subtotal: string
    calculated_total: string
    utility_energy_subtotal?: string
    utility_full_bill_total?: string
    energy_subtotal_difference?: string
    complete_bill_difference?: string
    unexplained_difference?: string
  }
  display?: {
    usage: string
    calculated_energy_subtotal: string
    blended_energy_rate?: string
    calculated_total: string
    utility_energy_subtotal?: string
    utility_full_bill_total?: string
    energy_subtotal_difference?: string
    complete_bill_difference?: string
  }
  tiers?: Array<{
    tier_id: string
    name: string
    lower_bound_kwh: string
    upper_bound_kwh: string | null
    display_range: string
  }>
  disclosure?: string
}

interface EvidencePage {
  bill_import_id: string
  artifact_id: string
  page_number: number
  parser_version: string
  fields: Array<{
    field_key: string
    source_excerpt?: string
    text_region?: Record<string, unknown>
    method: string
    confidence: string
  }>
}

const steps = [
  'Select account',
  'Upload PDF',
  'Inspect document',
  'Account & cycle',
  'Rate rules',
  'Confidence & conflicts',
  'Calculation preview',
  'Review outputs',
  'Apply to custom draft',
]

const editableDraftFields = [
  ['plan_name', 'Plan name'],
  ['plan_code', 'Plan code'],
  ['utility', 'Utility'],
  ['description', 'Description'],
  ['currency', 'Currency'],
  ['timezone', 'Timezone'],
  ['effective_from', 'Effective date'],
  ['effective_through', 'Optional end date'],
] as const

const reviewValue = (value: unknown): string => {
  if (value === null || value === undefined) return ''
  if (typeof value === 'object') return JSON.stringify(value)
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') {
    return `${value}`
  }
  return ''
}

const correctionValue = (source: unknown, value: string): unknown => {
  if (typeof source === 'number') return Number(value)
  if (source !== null && typeof source === 'object') {
    try {
      return JSON.parse(value) as unknown
    } catch {
      return value
    }
  }
  return value
}

const confidenceStatus = (confidence: string) => {
  if (confidence === 'administrator_confirmed' || confidence === 'high') return 'healthy'
  if (confidence === 'medium') return 'pending'
  return 'failed'
}

interface RuntimeBillImport extends Partial<Omit<BillImport, 'normalized' | 'cycle_draft'>> {
  normalized?: Partial<BillImport['normalized']>
  cycle_draft?: Partial<CycleDraft>
}

function normalizeBillImport(value: BillImport): BillImport {
  const candidate = value as unknown as RuntimeBillImport
  const cycleDraft = candidate.cycle_draft
    ? {
        ...(candidate.cycle_draft as CycleDraft),
        status: candidate.cycle_draft.status || 'draft',
        usage_by_tier: Array.isArray(candidate.cycle_draft.usage_by_tier) ? candidate.cycle_draft.usage_by_tier : [],
        usage_by_tou: Array.isArray(candidate.cycle_draft.usage_by_tou) ? candidate.cycle_draft.usage_by_tou : [],
        meter_records: Array.isArray(candidate.cycle_draft.meter_records) ? candidate.cycle_draft.meter_records : [],
      }
    : undefined
  return {
    ...value,
    status: candidate.status || 'review_required',
    source_role: candidate.source_role || 'supporting',
    extraction_method: candidate.extraction_method || 'unavailable',
    parser_version: candidate.parser_version || 'unavailable',
    page_count: Number.isFinite(candidate.page_count) && Number(candidate.page_count) > 0 ? Number(candidate.page_count) : 1,
    blocking_warnings: Array.isArray(candidate.blocking_warnings) ? candidate.blocking_warnings : [],
    extraction_warnings: Array.isArray(candidate.extraction_warnings) ? candidate.extraction_warnings : [],
    fields: Array.isArray(candidate.fields) ? candidate.fields : [],
    conflicts: Array.isArray(candidate.conflicts) ? candidate.conflicts : [],
    normalized: {
      account: candidate.normalized?.account ?? {},
      rate_plan: candidate.normalized?.rate_plan ?? {},
      billing_cycle: candidate.normalized?.billing_cycle ?? {},
    },
    cycle_draft: cycleDraft,
  }
}

function saveBlob(blob: Blob, filename: string) {
  const href = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = href
  link.download = filename
  link.click()
  URL.revokeObjectURL(href)
}

function ImporterErrorState({
  error,
  retry,
  retrying = false,
  canContinueWithoutAccount = false,
  continueWithoutAccount,
}: {
  error: AppError
  retry?: () => void
  retrying?: boolean
  canContinueWithoutAccount?: boolean
  continueWithoutAccount?: () => void
}) {
  return (
    <section className="app-error-boundary" role="alert" data-error-code={error.code}>
      <AlertTriangle aria-hidden="true" />
      <div>
        <h3>{error.title}</h3>
        <p>{error.message}</p>
        <p className="diagnostic-note">Reference: <code>{error.correlation_id}</code></p>
        <div className="inline-actions">
          {retry && error.retryable && (
            <button
              type="button"
              className="button secondary"
              disabled={retrying}
              onClick={retry}
            >
              {retrying ? 'Retrying…' : 'Retry'}
            </button>
          )}
          {canContinueWithoutAccount && continueWithoutAccount && (
            <button
              type="button"
              className="button secondary"
              onClick={continueWithoutAccount}
            >
              Continue without account
            </button>
          )}
          <Link className="button secondary" to="/billing/rate-plans">Return to Rate Plans</Link>
        </div>
        <details className="technical-details">
          <summary>Technical details</summary>
          <p>Code: {error.code}</p>
          {error.technical_details && <pre>{error.technical_details}</pre>}
        </details>
      </div>
    </section>
  )
}

const localNoAccountContext = (): UtilityAccountRateContext => ({
  schema_version: 'utility-account-rate-context/1.0',
  api_version: '1.0.0',
  backend_version: 'unavailable',
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
})

export function BillImportWorkspace({
  currentDraft,
  editorMode,
  onApplyDraft,
  onClose,
}: {
  currentDraft: RatePlanDocument
  editorMode: 'new_custom_plan' | 'existing_custom_plan_draft'
  onApplyDraft: (document: RatePlanDocument) => void
  onClose: () => void
}) {
  const [searchParams, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [step, setStep] = useState(searchParams.get('bill_id') ? 2 : 0)
  const [accountId, setAccountId] = useState(searchParams.get('account_id') ?? '')
  const [billId, setBillId] = useState(searchParams.get('bill_id') ?? '')
  const [retentionMode, setRetentionMode] = useState<RetentionMode>('retain')
  const [retainUntil, setRetainUntil] = useState('')
  const [sourceRole, setSourceRole] = useState<SourceRole>('supporting')
  const [selectedPage, setSelectedPage] = useState(1)
  const [threshold, setThreshold] = useState<ThresholdInterpretation>('unknown')
  const [fieldActions, setFieldActions] = useState<Record<string, FieldAction>>({})
  const [corrections, setCorrections] = useState<Record<string, string>>({})
  const [conflictDecisions, setConflictDecisions] = useState<Record<string, string>>({})
  const [message, setMessage] = useState('')
  const [draftChoices, setDraftChoices] = useState<Record<string, 'import' | 'keep' | 'manual'>>({})
  const [manualDraftValues, setManualDraftValues] = useState<Record<string, string>>({})
  const [continueWithoutContext, setContinueWithoutContext] = useState(false)
  const [contextRetryCount, setContextRetryCount] = useState(0)
  const uploadKey = useRef<string | null>(null)

  const accountContext = useQuery({
    queryKey: ['utility-bill-import-context', accountId || 'no-account'],
    queryFn: async ({ signal }) => {
      const query = accountId ? `?account_id=${encodeURIComponent(accountId)}` : ''
      return parseUtilityAccountRateContext(
        await api<unknown>(`/api/v1/admin/utility-bill-import-context${query}`, { signal }),
      )
    },
    retry: false,
    placeholderData: (previousContext) => previousContext,
  })
  const history = useQuery({
    queryKey: ['utility-bill-imports', accountId],
    queryFn: () => api<BillSummary[]>(`/api/v1/admin/utility-bill-imports${accountId ? `?utility_account_id=${encodeURIComponent(accountId)}` : ''}`),
  })
  const bill = useQuery({
    queryKey: ['utility-bill-import', billId],
    queryFn: () => api<BillImport>(`/api/v1/admin/utility-bill-imports/${billId}`),
    enabled: Boolean(billId),
    select: normalizeBillImport,
  })
  const comparison = useQuery({
    queryKey: ['utility-bill-comparison', billId],
    queryFn: () => api<BillComparison>(`/api/v1/admin/utility-bill-imports/${billId}/comparison`),
    enabled: Boolean(billId),
  })
  const linkedDraft = useQuery({
    queryKey: ['rate-version', bill.data?.rate_version_id],
    queryFn: () => api<{ version: Record<string, unknown>; document: RatePlanDocument }>(`/api/v1/rates/versions/${bill.data?.rate_version_id}`),
    enabled: Boolean(bill.data?.rate_version_id),
  })
  const evidence = useQuery({
    queryKey: ['utility-bill-evidence-page', billId, selectedPage],
    queryFn: () => api<EvidencePage>(`/api/v1/admin/utility-bill-imports/${billId}/evidence/pages/${selectedPage}`),
    enabled: Boolean(billId && step === 2),
  })

  useEffect(() => {
    if (!bill.data) return
    setThreshold(bill.data.cycle_draft?.threshold_interpretation ?? 'unknown')
    setSourceRole(bill.data.source_role)
    setRetentionMode(bill.data.retention_mode)
    const actions: Record<string, FieldAction> = {}
    const values: Record<string, string> = {}
    for (const field of bill.data.fields) {
      actions[field.id] = field.review_state === 'confirmed'
        ? 'confirm'
        : field.review_state === 'corrected'
          ? 'correct'
          : field.review_state === 'rejected'
            ? 'reject'
            : 'review'
      values[field.id] = reviewValue(field.effective_value)
    }
    setFieldActions(actions)
    setCorrections(values)
  }, [bill.data])

  useEffect(() => {
    if (accountId || !bill.data?.utility_account_id) return
    setAccountId(bill.data.utility_account_id)
  }, [accountId, bill.data?.utility_account_id])

  const selectBill = (id: string) => {
    setBillId(id)
    setSearchParams((currentParams) => {
      const next = new URLSearchParams(currentParams)
      next.set('bill_id', id)
      if (accountId) next.set('account_id', accountId)
      return next
    })
    setStep(2)
    setMessage('')
  }

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const body = new FormData()
      body.append('upload', file)
      const query = new URLSearchParams({
        retention_mode: retentionMode,
        source_role: sourceRole,
      })
      if (retentionMode === 'retain_until' && retainUntil) {
        query.set('retain_until', new Date(retainUntil).toISOString())
      }
      if (accountId) query.set('account_id', accountId)
      else {
        query.set('timezone', currentDraft.timezone)
        query.set('currency', currentDraft.currency)
      }
      const nextUploadKey = uploadKey.current ??
        `${file.name}:${file.size}:${file.lastModified}:${crypto.randomUUID()}`
      uploadKey.current = nextUploadKey
      return api<BillImport>(
        `/api/v1/admin/utility-bill-imports?${query.toString()}`,
        {
          method: 'POST',
          body,
          headers: { 'X-Idempotency-Key': nextUploadKey },
        },
      )
    },
    onSuccess: async (result) => {
      uploadKey.current = null
      setBillId(result.id)
      setSearchParams((currentParams) => {
        const next = new URLSearchParams(currentParams)
        next.set('bill_id', result.id)
        if (accountId) next.set('account_id', accountId)
        else next.delete('account_id')
        return next
      })
      setStep(2)
      setMessage('The PDF was inspected locally. Separate rate-plan and billing-cycle drafts are ready for review.')
      await queryClient.invalidateQueries({ queryKey: ['utility-bill-imports'] })
    },
  })

  const review = useMutation({
    mutationFn: () => {
      if (!bill.data) throw new Error('No bill is selected')
      const fieldReviews = bill.data.fields.flatMap((field) => {
        const action = fieldActions[field.id] ?? 'review'
        if (action === 'review') return []
        return [{
          field_id: field.id,
          action,
          ...(action === 'correct'
            ? { value: correctionValue(field.normalized_value, corrections[field.id] ?? '') }
            : {}),
        }]
      })
      const conflictResolutions = bill.data.conflicts.flatMap((conflict) => {
        const decision = conflictDecisions[conflict.id]
        return decision
          ? [{ conflict_id: conflict.id, decision, note: 'Administrator reviewed in utility-bill import workflow' }]
          : []
      })
      return api<BillImport>(`/api/v1/admin/utility-bill-imports/${bill.data.id}/review`, {
        method: 'PUT',
        body: JSON.stringify({
          revision: bill.data.revision,
          field_reviews: fieldReviews,
          conflict_resolutions: conflictResolutions,
          threshold_interpretation: threshold,
          source_role: sourceRole,
        }),
      })
    },
    onSuccess: async (result) => {
      setMessage(result.status === 'ready_to_publish'
        ? 'Review saved. The extraction is ready for rate-engine validation and explicit publication.'
        : 'Review saved. Resolve the remaining blockers before publication.')
      await bill.refetch()
      await comparison.refetch()
      await history.refetch()
    },
  })

  const validate = useMutation({
    mutationFn: () => api<{ bill_status: string; blocking_warnings: BillImport['blocking_warnings']; validation: { valid: boolean; errors: Array<{ code: string; message: string; path: string }>; warnings: Array<{ code: string; message: string; path: string }> } }>(
      `/api/v1/admin/utility-bill-imports/${billId}/validate`,
      { method: 'POST' },
    ),
    onSuccess: (result) => {
      setMessage(result.validation.valid
        ? 'The linked rate draft passed the existing rate engine validation.'
        : 'The linked rate draft still has blocking rate-engine validation errors.')
    },
  })

  const importCycle = useMutation({
    mutationFn: () => api<{ status: string }>(
      `/api/v1/admin/utility-bill-imports/${billId}/import-billing-cycle`,
      { method: 'POST' },
    ),
    onSuccess: async () => {
      setMessage('The reviewed billing-cycle draft was imported without overwriting monitored readings.')
      await bill.refetch()
      await comparison.refetch()
    },
  })

  const retention = useMutation({
    mutationFn: () => {
      if (!bill.data) throw new Error('No bill is selected')
      return api<BillImport>(`/api/v1/admin/utility-bill-imports/${bill.data.id}/retention`, {
        method: 'PUT',
        body: JSON.stringify({
          revision: bill.data.revision,
          retention_mode: retentionMode,
          retain_until: retentionMode === 'retain_until' && retainUntil
            ? new Date(retainUntil).toISOString()
            : null,
        }),
      })
    },
    onSuccess: async () => {
      setMessage('Original-PDF retention was updated. Sanitized evidence and audit provenance remain retained.')
      await bill.refetch()
      await history.refetch()
    },
  })

  const removeOriginal = useMutation({
    mutationFn: () => api<{ original_available: boolean }>(
      `/api/v1/admin/utility-bill-imports/${billId}/original`,
      { method: 'DELETE' },
    ),
    onSuccess: async () => {
      setMessage('The original PDF was removed. Sanitized evidence, normalized values, and audit history remain.')
      await bill.refetch()
      await history.refetch()
    },
  })

  const attachAccount = useMutation({
    mutationFn: () => {
      if (!bill.data || !accountId) {
        throw new Error('A bill import and utility account are required')
      }
      return api<BillImport>(
        `/api/v1/admin/utility-bill-imports/${bill.data.id}/account-context`,
        {
          method: 'PUT',
          body: JSON.stringify({
            revision: bill.data.revision,
            account_id: accountId,
          }),
        },
      )
    },
    onSuccess: async () => {
      setMessage('The account context is attached. Rate publication and billing-cycle application still require separate review actions.')
      await bill.refetch()
      await history.refetch()
    },
  })

  const effectiveContext = continueWithoutContext
    ? localNoAccountContext()
    : accountContext.data
  const availableAccounts = effectiveContext?.available_accounts ?? []
  const selectedAccount = effectiveContext?.account ?? null
  const currentPlan = getCurrentPlan(effectiveContext)
  const readiness = getRateContextReadiness(effectiveContext)
  const importerMode = resolveImporterMode({
    accountId: effectiveContext?.account_id ?? (accountId || null),
    currentPlan,
    existingDraft: editorMode === 'existing_custom_plan_draft',
    clonedFromVersionId: currentDraft.cloned_from_rate_version_id,
    legacyRedirect: searchParams.get('legacy_import') === '1',
    newCustomPlan: editorMode === 'new_custom_plan',
  })
  const fieldGroups = useMemo(() => {
    const groups: {
      account: BillField[]
      billing_cycle: BillField[]
      rate_plan: BillField[]
    } = { account: [], billing_cycle: [], rate_plan: [] }
    for (const field of bill.data?.fields ?? []) {
      if (String(field.output_kind) === 'account') groups.account.push(field)
      else if (String(field.output_kind) === 'billing_cycle') groups.billing_cycle.push(field)
      else if (String(field.output_kind) === 'rate_plan') groups.rate_plan.push(field)
    }
    return groups
  }, [bill.data?.fields])
  const current = bill.data
  const mayContinue = step < 2 || Boolean(current)
  const contextError = accountContext.error ? toAppError(accountContext.error) : null
  const importerState: BillImportState<BillImport> = useMemo(() => {
    if (!continueWithoutContext && (accountContext.isLoading || accountContext.isFetching)) {
      return { status: 'initializing', draft: currentDraft }
    }
    if (!continueWithoutContext && contextError) {
      return { status: 'recoverable_error', draft: currentDraft, error: contextError }
    }
    if (upload.isPending) {
      return { status: 'uploading', draft: currentDraft, progress: 25 }
    }
    if (billId && bill.isLoading) {
      return { status: 'extracting', draft: currentDraft, job_id: billId }
    }
    if (current) {
      return { status: 'review', draft: currentDraft, extraction: current }
    }
    return { status: 'ready_for_upload', draft: currentDraft, mode: importerMode }
  }, [
    accountContext.isFetching,
    accountContext.isLoading,
    bill.isLoading,
    billId,
    contextError,
    continueWithoutContext,
    current,
    currentDraft,
    importerMode,
    upload.isPending,
  ])

  function submitUpload(event: FormEvent) {
    event.preventDefault()
    const file = fileInput.current?.files?.[0]
    if (file) upload.mutate(file)
  }

  async function download(path: 'original' | 'sanitized-evidence') {
    const blob = await apiDownload(`/api/v1/admin/utility-bill-imports/${billId}/${path}`)
    saveBlob(blob, path === 'original' ? `utility-bill-${billId}.pdf` : `utility-bill-${billId}-evidence.json`)
  }

  function applyImportedRateDraft() {
    const imported = linkedDraft.data?.document
    if (!imported) return
    const next = structuredClone(currentDraft)
    for (const [key] of editableDraftFields) {
      const choice = draftChoices[key] ?? 'keep'
      if (choice === 'import') {
        next[key] = imported[key] as never
      } else if (choice === 'manual') {
        next[key] = (key === 'effective_through'
          ? manualDraftValues[key] || null
          : manualDraftValues[key] ?? '') as never
      }
    }
    if ((draftChoices.tariff_rules ?? 'keep') === 'import') {
      next.pricing_model = imported.pricing_model
      next.flat_rate_per_kwh = imported.flat_rate_per_kwh
      next.billing_cycle = structuredClone(imported.billing_cycle)
      next.tiers = structuredClone(imported.tiers)
      next.hybrid_pricing = structuredClone(imported.hybrid_pricing)
      next.seasons = structuredClone(imported.seasons)
      next.adjustments = structuredClone(imported.adjustments)
      next.provider_mode = imported.provider_mode
      next.cost_scope_default = imported.cost_scope_default
    }
    if ((draftChoices.source_evidence ?? 'keep') === 'import') {
      next.source_label = imported.source_label
      next.source_note = imported.source_note
      next.cloned_from_rate_version_id = imported.cloned_from_rate_version_id
    }
    onApplyDraft(next)
    onClose()
  }

  return <>
    <header className="bill-import-workspace-header">
      <div><span className="eyebrow">Private administrator workflow inside Custom Plan</span><h2>Import utility bill</h2><p>Extract account-specific evidence locally, review separate outputs, then choose which reviewed fields enter the unsaved Custom Plan draft.</p></div>
      <button type="button" className="button secondary" onClick={onClose}>Close importer</button>
    </header>

    <nav className="bill-import-steps" aria-label="Utility bill import steps">
      {steps.map((label, index) => (
        <button
          type="button"
          key={label}
          className={step === index ? 'active' : ''}
          aria-current={step === index ? 'step' : undefined}
          disabled={index >= 2 && !current}
          onClick={() => { setStep(index); }}
        >
          <span>{index + 1}</span>{label}
        </button>
      ))}
    </nav>

    {importerState.status === 'initializing' && (
      <LoadingState label="Loading utility-account and rate context…" />
    )}
    {importerState.status === 'recoverable_error' && (
      <ImporterErrorState
        error={importerState.error}
        retrying={accountContext.isFetching}
        retry={
          contextRetryCount < 3
            ? () => {
                setContextRetryCount((value) => value + 1)
                void accountContext.refetch()
              }
            : undefined
        }
        canContinueWithoutAccount={!(accountContext.error instanceof AppContractError)}
        continueWithoutAccount={() => {
          setAccountId('')
          setContinueWithoutContext(true)
          setSearchParams((currentParams) => {
            const next = new URLSearchParams(currentParams)
            next.delete('account_id')
            return next
          })
        }}
      />
    )}
    {importerState.status === 'uploading' && (
      <p className="form-success" role="status">
        Uploading and inspecting the PDF locally… {importerState.progress}%
      </p>
    )}
    {importerState.status === 'extracting' && (
      <LoadingState label="Loading extracted bill evidence…" />
    )}

    {message && <p className="form-success" role="status">{message}</p>}
    {upload.error && <ErrorState error={upload.error} />}
    {review.error && <ErrorState error={review.error} />}
    {validate.error && <ErrorState error={validate.error} />}
    {importCycle.error && <ErrorState error={importCycle.error} />}
    {retention.error && <ErrorState error={retention.error} />}
    {removeOriginal.error && <ErrorState error={removeOriginal.error} />}
    {attachAccount.error && <ErrorState error={attachAccount.error} />}

    {step === 0 && <Panel title="Select utility account" eyebrow="Step 1 · Account scope">
      <label className="bill-account-select">
        <span>Utility account (optional)</span>
        <select
          value={accountId}
          disabled={!continueWithoutContext && !accountContext.data}
          onChange={(event) => {
            const nextAccountId = event.target.value
            setAccountId(nextAccountId)
            setContinueWithoutContext(false)
            setContextRetryCount(0)
            setSearchParams((currentParams) => {
              const next = new URLSearchParams(currentParams)
              if (nextAccountId) next.set('account_id', nextAccountId)
              else next.delete('account_id')
              return next
            })
          }}
        >
          <option value="">Continue without an account</option>
          {availableAccounts.map((account) => (
            <option value={account.id} key={account.id}>
              {account.site_name} · {account.name}
            </option>
          ))}
        </select>
      </label>
      {!accountId && (
        <div className="setup-callout">
          <strong>Account assignment is optional during extraction.</strong>
          <p>Select a utility account later to apply billing-cycle data.</p>
          {!availableAccounts.length && (
            <Link className="button secondary" to="/billing/accounts">Create utility account</Link>
          )}
        </div>
      )}
      {selectedAccount && (
        <>
          <dl className="detail-list bill-account-context">
            <div><dt>Utility</dt><dd>{selectedAccount.utility_name}</dd></div>
            <div><dt>Provider</dt><dd>{formatStructuredLabel(selectedAccount.provider_mode)}</dd></div>
            <div><dt>Timezone</dt><dd>{selectedAccount.timezone}</dd></div>
            <div><dt>Current plan</dt><dd>{currentPlan?.name ?? 'Not assigned'}</dd></div>
          </dl>
          {!readiness.rate_assigned && (
            <p className="privacy-notice">
              <ShieldCheck size={18} />
              <span>No rate plan is currently assigned. Imported values will prefill a new custom plan.</span>
            </p>
          )}
          {readiness.rate_assigned && (
            <p className="privacy-notice">
              <ShieldCheck size={18} />
              <span>The assigned plan is comparison context only. It will not be modified.</span>
            </p>
          )}
        </>
      )}
    </Panel>}

    {step === 1 && <div className="bill-import-columns">
      <Panel title="Upload password-free PDF" eyebrow="Step 2 · Local processing">
        <form className="stack-form" onSubmit={submitUpload}>
          <label><span>Utility-bill PDF</span><input ref={fileInput} type="file" accept="application/pdf,.pdf" required /></label>
          <div className="form-columns">
            <label><span>Source role</span><select value={sourceRole} onChange={(event) => { setSourceRole(event.target.value as SourceRole); }}><option value="supporting">Supporting source (recommended)</option><option value="authoritative_account_specific" disabled={!accountId}>Authoritative account-specific source</option><option value="reference_only">Reference only</option></select></label>
            <label><span>Original PDF retention</span><select value={retentionMode} onChange={(event) => { setRetentionMode(event.target.value as RetentionMode); }}><option value="retain">Retain original</option><option value="retain_until">Retain until date</option><option value="delete_after_approval">Delete after approved extraction</option></select></label>
          </div>
          {retentionMode === 'retain_until' && <label><span>Retain until</span><input type="datetime-local" value={retainUntil} onChange={(event) => { setRetainUntil(event.target.value); }} required /></label>}
          <p className="privacy-notice"><ShieldCheck size={18} /><span>Processing stays on this Power Monitor server. The browser does not store document text. A single bill never activates a rate automatically.</span></p>
          {!accountId && <p className="diagnostic-note">Tariff extraction is available now. Account assignment and billing-cycle application remain deferred.</p>}
          <button className="button primary" disabled={upload.isPending || (!continueWithoutContext && accountContext.error instanceof AppContractError)}><FileUp size={16} /> {upload.isPending ? 'Inspecting and extracting…' : 'Upload and create drafts'}</button>
        </form>
      </Panel>
      <Panel title="Prior imports" eyebrow="Billing-cycle history">
        {history.isLoading ? <LoadingState /> : history.error ? <ErrorState error={history.error} /> : history.data?.length ? <div className="bill-history-list">{history.data.map((item) => <button type="button" key={item.id} onClick={() => { selectBill(item.id); }}><span><strong>{item.utility_account_name}</strong><small>{formatTime(item.created_at)} · {formatStructuredLabel(item.extraction_method)} · {item.page_count} page{item.page_count === 1 ? '' : 's'}</small></span><StatusPill status={item.status === 'published' ? 'healthy' : item.status === 'ready_to_publish' ? 'pending' : 'failed'} label={formatStructuredLabel(item.status)} /></button>)}</div> : <EmptyState title="No uploaded bills" message="The first import for this account will appear here." />}
      </Panel>
    </div>}

    {step === 2 && current && <div className="bill-import-columns">
      <Panel title="Document inspection" eyebrow="Step 3 · Content and parser">
        <dl className="detail-list">
          <div><dt>SHA-256</dt><dd><code title={current.content_sha256}>{current.content_sha256}</code></dd></div>
          <div><dt>Pages</dt><dd>{current.page_count}</dd></div>
          <div><dt>Extraction</dt><dd>{formatStructuredLabel(current.extraction_method)}</dd></div>
          <div><dt>Parser</dt><dd>{current.parser_version}</dd></div>
          <div><dt>Automatic activation</dt><dd>Disabled</dd></div>
        </dl>
        <div className="inline-actions">
          {current.original_available && <button className="button secondary" onClick={() => void download('original')}><Download size={15} /> Private original</button>}
          <button className="button secondary" onClick={() => void download('sanitized-evidence')}><Download size={15} /> Sanitized evidence</button>
        </div>
        {[...current.extraction_warnings, ...current.blocking_warnings].map((warning, index) => <p className="billing-warning" key={`${warning.code}-${index}`}><AlertTriangle size={15} /><span>{warning.message ?? warning.code}</span></p>)}
      </Panel>
      <AppErrorBoundary
        scope="PDF evidence viewer"
        resetKey={`${billId}:${selectedPage}`}
        onRetry={() => { void evidence.refetch() }}
        administrator
      >
        <Panel title={`Evidence page ${selectedPage}`} eyebrow="Source excerpts and coordinates" actions={<select aria-label="Evidence page" value={selectedPage} onChange={(event) => { setSelectedPage(Number(event.target.value)); }}>{Array.from({ length: current.page_count }, (_, index) => <option value={index + 1} key={index + 1}>Page {index + 1}</option>)}</select>}>
          {evidence.isLoading ? <LoadingState /> : evidence.error ? <ErrorState error={evidence.error} /> : evidence.data?.fields.length ? <div className="evidence-list">{evidence.data.fields.map((field) => <article key={field.field_key}><header><strong>{formatStructuredLabel(field.field_key)}</strong><StatusPill status={confidenceStatus(field.confidence)} label={formatStructuredLabel(field.confidence)} /></header><blockquote>{field.source_excerpt || 'No retained excerpt'}</blockquote><small>{formatStructuredLabel(field.method)} · {field.text_region ? JSON.stringify(field.text_region) : 'coordinates unavailable'}</small></article>)}</div> : <EmptyState title="No fields on this page" message="Other pages may contain the extracted evidence." />}
        </Panel>
      </AppErrorBoundary>
    </div>}

    {step === 3 && current && <div className="bill-import-columns">
      {current.utility_account_id === null && (
        <Panel title="Account assignment deferred" eyebrow="Safe unassigned import">
          <p className="panel-copy">Select a utility account above when you are ready to apply billing-cycle evidence. Extracted tariff values and your Custom Plan draft remain available without one.</p>
          {accountId ? (
            <button
              type="button"
              className="button secondary"
              disabled={attachAccount.isPending}
              onClick={() => { attachAccount.mutate() }}
            >
              {attachAccount.isPending ? 'Attaching account…' : `Attach ${selectedAccount?.name ?? 'selected account'}`}
            </button>
          ) : (
            <button type="button" className="button secondary" onClick={() => { setStep(0) }}>
              Select an account
            </button>
          )}
        </Panel>
      )}
      <FieldReviewPanel title="Account fields" eyebrow="Step 4 · Account matching" fields={fieldGroups.account} actions={fieldActions} corrections={corrections} onAction={(id, action) => { setFieldActions((value) => ({ ...value, [id]: action })); }} onCorrection={(id, value) => { setCorrections((currentValue) => ({ ...currentValue, [id]: value })); }} />
      <FieldReviewPanel title="Billing-cycle fields" eyebrow="Separate cycle draft" fields={fieldGroups.billing_cycle} actions={fieldActions} corrections={corrections} onAction={(id, action) => { setFieldActions((value) => ({ ...value, [id]: action })); }} onCorrection={(id, value) => { setCorrections((currentValue) => ({ ...currentValue, [id]: value })); }} />
      {current.cycle_draft && <Panel title="Billing-cycle draft" eyebrow="Bill-specific output">
        <dl className="bill-cycle-grid">
          <div><dt>Period</dt><dd>{current.cycle_draft.starts_at && current.cycle_draft.ends_at ? formatBillingPeriod(current.cycle_draft.starts_at, current.cycle_draft.ends_at) : 'Unavailable'}</dd></div>
          <div><dt>Usage</dt><dd>{formatEnergy(current.cycle_draft.total_usage_kwh)}</dd></div>
          <div><dt>Energy subtotal</dt><dd>{formatCurrency(current.cycle_draft.energy_subtotal)}</dd></div>
          <div><dt>Complete bill total</dt><dd>{formatCurrency(current.cycle_draft.full_bill_total)}</dd></div>
          <div><dt>Current / projected tier</dt><dd>{current.cycle_draft.current_tier ?? 'Unavailable'} / {current.cycle_draft.projected_tier ?? 'Unavailable'}</dd></div>
          <div><dt>Import state</dt><dd>{formatStructuredLabel(current.cycle_draft.status)}</dd></div>
        </dl>
        <p className="panel-copy">This bill-specific record remains separate from recurring tariff rules. Credits, taxes, and unexplained adjustments are not copied into the rate plan automatically.</p>
      </Panel>}
    </div>}

    {step === 4 && current && <>
      <FieldReviewPanel title="Extracted rate-plan rules" eyebrow="Step 5 · Reusable tariff draft" fields={fieldGroups.rate_plan} actions={fieldActions} corrections={corrections} onAction={(id, action) => { setFieldActions((value) => ({ ...value, [id]: action })); }} onCorrection={(id, value) => { setCorrections((currentValue) => ({ ...currentValue, [id]: value })); }} />
      <Panel title="Tier preview" eyebrow="Structured numeric bounds">
        {current.normalized.rate_plan.tiers?.length ? <div className="responsive-table bill-tier-table"><table><thead><tr><th>Tier</th><th>Range</th><th>Reported usage</th><th>Configured rate</th><th>Reported charge</th></tr></thead><tbody>{current.normalized.rate_plan.tiers.map((tier, index) => <tr key={`${tier.name}-${index}`}><td data-label="Tier">{tier.name ?? `Tier ${index + 1}`}</td><td data-label="Range">{formatTierRange(tier.lower_bound_kwh ?? '0', tier.upper_bound_kwh)}</td><td data-label="Reported usage">{formatEnergy(tier.usage_kwh)}</td><td data-label="Configured rate">{formatEnergyRate(tier.price_per_kwh)}</td><td data-label="Reported charge">{formatCurrency(tier.energy_charge)}</td></tr>)}</tbody></table></div> : <EmptyState title="No complete tier table detected" message="Correct or reject the incomplete extracted fields here; the Custom Plan draft remains unchanged until Step 9." />}
      </Panel>
    </>}

    {step === 5 && current && <div className="bill-import-columns">
      <Panel title="Confidence review" eyebrow="Step 6 · Administrator confirmation">
        <div className="confidence-summary">
          {['administrator_confirmed', 'high', 'medium', 'low', 'missing'].map((confidence) => <article key={confidence}><span>{formatStructuredLabel(confidence)}</span><strong>{current.fields.filter((field) => field.confidence === confidence).length}</strong></article>)}
        </div>
        <label><span>Threshold interpretation</span><select value={threshold} onChange={(event) => { setThreshold(event.target.value as ThresholdInterpretation); }}><option value="unknown">Unknown — requires administrator review</option><option value="fixed_cycle_threshold">Fixed billing-cycle threshold</option><option value="daily_baseline">Derived from daily baseline</option><option value="baseline_multiplier">Derived from baseline multiplier</option></select></label>
        <label><span>Uploaded bill source role</span><select value={sourceRole} onChange={(event) => { setSourceRole(event.target.value as SourceRole); }}><option value="supporting">Supporting source</option><option value="authoritative_account_specific" disabled={current.utility_account_id === null}>Authoritative account-specific source</option><option value="reference_only">Reference only</option></select></label>
        <p className="field-help">A displayed threshold does not prove whether it is fixed, baseline-derived, seasonal, or account-specific.</p>
      </Panel>
      <Panel title="Source conflicts" eyebrow="Current configuration and managed evidence">
        {current.conflicts.length ? <div className="conflict-list">{current.conflicts.map((conflict) => <article key={conflict.id}><header><strong>{formatStructuredLabel(conflict.field_key)}</strong><StatusPill status={conflict.status === 'unresolved' ? 'failed' : 'healthy'} label={formatStructuredLabel(conflict.status)} /></header><dl><div><dt>Uploaded bill</dt><dd>{reviewValue(conflict.extracted_value)}</dd></div><div><dt>{formatStructuredLabel(conflict.comparison_source)}</dt><dd>{reviewValue(conflict.configured_value)}</dd></div></dl>{conflict.status === 'unresolved' && <label><span>Resolution</span><select value={conflictDecisions[conflict.id] ?? ''} onChange={(event) => { setConflictDecisions((value) => ({ ...value, [conflict.id]: event.target.value })); }}><option value="">Review required</option><option value="accepted_bill">Accept uploaded bill value</option><option value="accepted_configured">Keep configured value</option><option value="dismissed">Dismiss with recorded review</option></select></label>}</article>)}</div> : <EmptyState title="No detected conflicts" message="Applying values still requires explicit field review and rate-engine validation." />}
      </Panel>
    </div>}

    {step === 6 && current && <Panel title="Rate calculation preview" eyebrow="Step 7 · Exact engine result">
      {comparison.isLoading ? <LoadingState label="Calculating with the linked draft…" /> : comparison.error ? <ErrorState error={comparison.error} /> : !comparison.data?.available ? <EmptyState title="Preview not yet available" message={comparison.data?.reason ?? 'Review and correct the extracted rate rules and cycle fields.'} action={<button className="button secondary" onClick={() => { setStep(4) }}>Review extracted rate rules</button>} /> : <>
        <section className="bill-comparison-hero">
          <article><span>Calculated energy subtotal</span><strong>{comparison.data.display?.calculated_energy_subtotal}</strong><small>Existing exact rate engine</small></article>
          <article><span>Derived blended rate</span><strong>{comparison.data.display?.blended_energy_rate}</strong><small>Four-decimal display only</small></article>
          <article><span>Utility energy subtotal</span><strong>{comparison.data.display?.utility_energy_subtotal ?? 'Unavailable'}</strong><small>Extracted bill evidence</small></article>
          <article><span>Complete utility bill</span><strong>{comparison.data.display?.utility_full_bill_total ?? 'Unavailable'}</strong><small>Not interchangeable with energy subtotal</small></article>
        </section>
        <dl className="bill-difference-grid"><div><dt>Energy-subtotal difference</dt><dd>{comparison.data.display?.energy_subtotal_difference ?? 'Unavailable'}</dd></div><div><dt>Complete-bill difference</dt><dd>{comparison.data.display?.complete_bill_difference ?? 'Unavailable'}</dd></div><div><dt>Extraction confidence</dt><dd>{formatStructuredLabel(comparison.data.extraction_confidence)}</dd></div><div><dt>Calculation correctness</dt><dd>{formatStructuredLabel(comparison.data.calculation_correctness)}</dd></div></dl>
        <p className="billing-disclosure">{comparison.data.disclosure}</p>
        <details className="exact-details"><summary>Exact unrounded comparison values</summary><pre>{JSON.stringify(comparison.data.exact, null, 2)}</pre></details>
      </>}
    </Panel>}

    {step === 7 && current && <div className="bill-import-columns">
      <Panel title="Save administrator review" eyebrow="Step 8 · Separate outputs">
        <dl className="detail-list"><div><dt>Rate-plan draft</dt><dd>{current.rate_plan_id} / {current.rate_version_id}</dd></div><div><dt>Billing-cycle draft</dt><dd>{current.cycle_draft?.id ?? 'Missing'}</dd></div><div><dt>Required review decisions</dt><dd>{current.fields.filter((field) => !['confirmed', 'corrected'].includes(field.review_state)).length} pending</dd></div><div><dt>Blocking warnings</dt><dd>{current.blocking_warnings.length}</dd></div></dl>
        <button className="button primary" disabled={review.isPending} onClick={() => { review.mutate(); }}><Save size={16} /> {review.isPending ? 'Saving review…' : 'Save reviewed fields and outputs'}</button>
        <p className="field-help">Choose Confirm, Correct, or Reject for required fields on the Account, Cycle, and Rate Rules steps. Saving does not publish or assign the rate.</p>
      </Panel>
      <Panel title="Original document retention" eyebrow="Privacy control">
        <label><span>Retention policy</span><select value={retentionMode} onChange={(event) => { setRetentionMode(event.target.value as RetentionMode); }}><option value="retain">Retain original</option><option value="retain_until">Retain until date</option><option value="delete_after_approval">Delete after approved extraction</option></select></label>
        {retentionMode === 'retain_until' && <label><span>Retain until</span><input type="datetime-local" value={retainUntil} onChange={(event) => { setRetainUntil(event.target.value); }} /></label>}
        <div className="inline-actions"><button className="button secondary" disabled={retention.isPending} onClick={() => { retention.mutate(); }}>Save retention</button>{current.original_available && <button className="button ghost danger-text" disabled={removeOriginal.isPending || !['ready_to_publish', 'published'].includes(current.status)} onClick={() => { if (window.confirm('Remove the original private PDF? Sanitized evidence and audit history will remain.')) removeOriginal.mutate() }}><Trash2 size={15} /> Delete original now</button>}</div>
      </Panel>
      <Panel title="Normalized outputs" eyebrow="Exact decimal strings">
        <details className="exact-details"><summary>Rate-plan extraction</summary><pre>{JSON.stringify(current.normalized.rate_plan, null, 2)}</pre></details>
        <details className="exact-details"><summary>Billing-cycle extraction</summary><pre>{JSON.stringify(current.normalized.billing_cycle, null, 2)}</pre></details>
        <p className="field-help">Normalized JSON and calculations keep exact decimal strings. Currency and energy formatting above is display-only.</p>
      </Panel>
    </div>}

    {step === 8 && current && <div className="bill-import-columns">
      <Panel title="Validate and apply reviewed tariff" eyebrow="Step 9 · Custom Plan draft merge">
        <StatusPill status={current.status === 'ready_to_publish' || current.status === 'published' ? 'healthy' : 'failed'} label={formatStructuredLabel(current.status)} />
        <p className="panel-copy">Choose every value deliberately. Applying changes only the unsaved Custom Plan editor draft; it does not publish, activate, or assign a rate.</p>
        {linkedDraft.isLoading ? <LoadingState label="Loading the reviewed linked rate draft…" /> : linkedDraft.error ? <ErrorState error={linkedDraft.error} retry={() => { void linkedDraft.refetch() }} /> : linkedDraft.data ? <div className="bill-draft-choice-list">
          {editableDraftFields.map(([key, label]) => {
            const choice = draftChoices[key] ?? 'keep'
            return <article key={key}>
              <div><strong>{label}</strong><small>Current: {reviewValue(currentDraft[key]) || 'blank'} · Extracted: {reviewValue(linkedDraft.data.document[key]) || 'blank'}</small></div>
              <label><span className="sr-only">{label} choice</span><select aria-label={`${label} choice`} value={choice} onChange={(event) => { setDraftChoices((value) => ({ ...value, [key]: event.target.value as 'import' | 'keep' | 'manual' })) }}><option value="keep">Keep current draft</option><option value="import">Use reviewed extraction</option><option value="manual">Enter manually</option></select></label>
              {choice === 'manual' && <label><span className="sr-only">Manual {label}</span><input aria-label={`Manual ${label}`} value={manualDraftValues[key] ?? reviewValue(currentDraft[key])} onChange={(event) => { setManualDraftValues((value) => ({ ...value, [key]: event.target.value })) }} /></label>}
            </article>
          })}
          <article><div><strong>Complete tariff rules</strong><small>Pricing model, exact tiers, billing-cycle thresholds, TOU schedules, charges, provider mode, and cost scope</small></div><label><span className="sr-only">Complete tariff rules choice</span><select aria-label="Complete tariff rules choice" value={draftChoices.tariff_rules ?? 'keep'} onChange={(event) => { setDraftChoices((value) => ({ ...value, tariff_rules: event.target.value as 'import' | 'keep' })) }}><option value="keep">Keep current draft rules</option><option value="import">Use reviewed extracted rules</option></select></label></article>
          <article><div><strong>Source evidence references</strong><small>Sanitized evidence remains linked to the import; choose whether to copy its source label and note into this editor draft.</small></div><label><span className="sr-only">Source evidence choice</span><select aria-label="Source evidence choice" value={draftChoices.source_evidence ?? 'keep'} onChange={(event) => { setDraftChoices((value) => ({ ...value, source_evidence: event.target.value as 'import' | 'keep' })) }}><option value="keep">Keep current source fields</option><option value="import">Use imported evidence fields</option></select></label></article>
        </div> : <EmptyState title="No linked rate draft" message="Review or reprocess the import so a separate rate-plan draft is available." />}
        {validate.data && <div className="validation-compact"><strong>{validate.data.validation.valid ? 'Rate-engine validation passed' : 'Validation failed'}</strong>{[...validate.data.validation.errors, ...validate.data.validation.warnings].map((issue) => <span key={`${issue.code}-${issue.path}`}>{issue.message} · {issue.path}</span>)}</div>}
        <div className="inline-actions"><button className="button secondary" disabled={validate.isPending} onClick={() => { validate.mutate(); }}><FileSearch size={15} /> Validate reviewed draft</button><button className="button primary" disabled={!linkedDraft.data || !['ready_to_publish', 'published'].includes(current.status) || !validate.data?.validation.valid} onClick={applyImportedRateDraft}><FileUp size={15} /> Apply selected values to Custom Plan</button></div>
      </Panel>
      <Panel title="Import billing cycle" eyebrow="Separate bill-specific record">
        <p className="panel-copy">This records exact cycle dates and utility-reported cumulative usage separately. It never overwrites immutable monitored readings.</p>
        <dl className="detail-list"><div><dt>Cycle</dt><dd>{current.cycle_draft?.starts_at && current.cycle_draft.ends_at ? formatBillingPeriod(current.cycle_draft.starts_at, current.cycle_draft.ends_at) : 'Unavailable'}</dd></div><div><dt>Reported usage</dt><dd>{formatEnergy(current.cycle_draft?.total_usage_kwh)}</dd></div><div><dt>Authority</dt><dd>{formatStructuredLabel(sourceRole)}</dd></div><div><dt>Status</dt><dd>{formatStructuredLabel(current.cycle_draft?.status)}</dd></div></dl>
        <button className="button secondary" disabled={importCycle.isPending || current.utility_account_id === null || !['ready_to_publish', 'published'].includes(current.status) || current.cycle_draft?.status === 'imported'} onClick={() => { importCycle.mutate(); }}><FileUp size={15} /> Import reviewed billing cycle</button>
      </Panel>
      <Panel title="Workflow record" eyebrow="Evidence retained">
        <dl className="detail-list"><div><dt>Original PDF</dt><dd>{current.original_available ? 'Private and retained' : `Removed ${formatTime(current.original_deleted_at)}`}</dd></div><div><dt>Sanitized evidence</dt><dd>Retained</dd></div><div><dt>Artifact hash</dt><dd><code>{current.content_sha256}</code></dd></div><div><dt>Exact source value example</dt><dd>{formatDecimalDetail(current.cycle_draft?.energy_subtotal)}</dd></div></dl>
      </Panel>
    </div>}

    <footer className="bill-import-footer">
      <button className="button secondary" disabled={step === 0} onClick={() => { setStep((value) => Math.max(0, value - 1)); }}><ArrowLeft size={15} /> Previous</button>
      <span>Step {step + 1} of {steps.length}</span>
      <button className="button primary" disabled={step === steps.length - 1 || !mayContinue} onClick={() => { setStep((value) => Math.min(steps.length - 1, value + 1)); }}>Next <ArrowRight size={15} /></button>
    </footer>
  </>
}

function FieldReviewPanel({
  title,
  eyebrow,
  fields,
  actions,
  corrections,
  onAction,
  onCorrection,
}: {
  title: string
  eyebrow: string
  fields: BillField[]
  actions: Record<string, FieldAction>
  corrections: Record<string, string>
  onAction: (id: string, action: FieldAction) => void
  onCorrection: (id: string, value: string) => void
}) {
  return <Panel title={title} eyebrow={eyebrow}>
    {!fields.length ? <EmptyState title="No fields extracted" message="Missing required values remain publication blockers." /> : <div className="bill-field-list">{fields.map((field) => {
      const action = actions[field.id] ?? 'review'
      return <article key={field.id} className={`bill-field bill-confidence-${field.confidence}`}>
        <header><div><strong>{formatStructuredLabel(field.field_key)}</strong><small>Page {field.page_number ?? 'unknown'} · {formatStructuredLabel(field.extraction_method)} · parser {field.parser_version || 'unavailable'}</small></div><StatusPill status={confidenceStatus(field.confidence)} label={formatStructuredLabel(field.confidence)} /></header>
        <dl><div><dt>Extracted</dt><dd>{reviewValue(field.raw_value) || 'Missing'}</dd></div><div><dt>Normalized</dt><dd>{reviewValue(field.normalized_value) || 'Missing'}</dd></div></dl>
        {field.source_excerpt && <blockquote>{field.source_excerpt}</blockquote>}
        {field.warnings.map((warning, index) => <p className="field-warning" key={`${warning.code}-${index}`}><AlertTriangle size={13} /> {warning.message ?? warning.code}</p>)}
        <div className="bill-field-review">
          <label><span>Administrator decision</span><select value={action} onChange={(event) => { onAction(field.id, event.target.value as FieldAction); }}><option value="review">Review required</option><option value="confirm">Confirm normalized value</option><option value="correct">Correct value</option><option value="reject">Reject / mark missing</option></select></label>
          {action === 'correct' && <label><span>Corrected exact value</span><input value={corrections[field.id] ?? ''} onChange={(event) => { onCorrection(field.id, event.target.value); }} /></label>}
        </div>
        {field.normalization_history.length > 0 && <details><summary>Normalization history</summary><pre>{JSON.stringify(field.normalization_history, null, 2)}</pre></details>}
      </article>
    })}</div>}
  </Panel>
}
