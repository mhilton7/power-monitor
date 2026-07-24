import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Download,
  ExternalLink,
  FileSearch,
  FileUp,
  Save,
  ShieldCheck,
  Trash2,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, apiDownload } from '../api'
import { EmptyState, ErrorState, LoadingState, PageTitle, Panel, StatusPill, formatTime } from '../components/UI'
import {
  formatBillingPeriod,
  formatCurrency,
  formatDecimalDetail,
  formatEnergy,
  formatEnergyRate,
  formatTierRange,
} from '../formatters'
import type { UtilityAccount } from '../types'

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
  utility_account_id: string
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
  rate_plan_id: string
  rate_version_id: string
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
  utility_account_id: string
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
  'Publish & assign',
]

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

function saveBlob(blob: Blob, filename: string) {
  const href = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = href
  link.download = filename
  link.click()
  URL.revokeObjectURL(href)
}

export function BillImportPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [step, setStep] = useState(0)
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

  const accounts = useQuery({
    queryKey: ['utility-accounts', 'bill-import'],
    queryFn: () => api<UtilityAccount[]>('/api/v1/utility-accounts'),
  })
  const history = useQuery({
    queryKey: ['utility-bill-imports', accountId],
    queryFn: () => api<BillSummary[]>(`/api/v1/admin/utility-bill-imports${accountId ? `?utility_account_id=${encodeURIComponent(accountId)}` : ''}`),
  })
  const bill = useQuery({
    queryKey: ['utility-bill-import', billId],
    queryFn: () => api<BillImport>(`/api/v1/admin/utility-bill-imports/${billId}`),
    enabled: Boolean(billId),
  })
  const comparison = useQuery({
    queryKey: ['utility-bill-comparison', billId],
    queryFn: () => api<BillComparison>(`/api/v1/admin/utility-bill-imports/${billId}/comparison`),
    enabled: Boolean(billId),
  })
  const evidence = useQuery({
    queryKey: ['utility-bill-evidence-page', billId, selectedPage],
    queryFn: () => api<EvidencePage>(`/api/v1/admin/utility-bill-imports/${billId}/evidence/pages/${selectedPage}`),
    enabled: Boolean(billId && step === 2),
  })

  useEffect(() => {
    const firstAccount = accounts.data?.[0]
    if (!accountId && firstAccount) setAccountId(firstAccount.id)
  }, [accountId, accounts.data])

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

  const selectBill = (id: string) => {
    setBillId(id)
    setSearchParams({ bill_id: id })
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
      return api<BillImport>(
        `/api/v1/admin/utility-accounts/${accountId}/bill-imports?${query.toString()}`,
        { method: 'POST', body },
      )
    },
    onSuccess: async (result) => {
      setBillId(result.id)
      setSearchParams({ bill_id: result.id })
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

  const publish = useMutation({
    mutationFn: () => api<{ status: string; rate_version_id: string; rate_assignment_id: string }>(
      `/api/v1/admin/utility-bill-imports/${billId}/publish-and-assign`,
      { method: 'POST', body: JSON.stringify({}) },
    ),
    onSuccess: async (result) => {
      setMessage(`Immutable rate version published and assigned (${result.status}).`)
      await bill.refetch()
      await queryClient.invalidateQueries({ queryKey: ['managed-rates'] })
      await queryClient.invalidateQueries({ queryKey: ['utility-accounts'] })
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

  const selectedAccount = accounts.data?.find((account) => account.id === accountId)
  const fieldGroups = useMemo(() => {
    const groups: {
      account: BillField[]
      billing_cycle: BillField[]
      rate_plan: BillField[]
    } = { account: [], billing_cycle: [], rate_plan: [] }
    for (const field of bill.data?.fields ?? []) groups[field.output_kind].push(field)
    return groups
  }, [bill.data?.fields])
  const current = bill.data
  const mayContinue = step < 2 || Boolean(current)

  function submitUpload(event: FormEvent) {
    event.preventDefault()
    const file = fileInput.current?.files?.[0]
    if (file) upload.mutate(file)
  }

  async function download(path: 'original' | 'sanitized-evidence') {
    const blob = await apiDownload(`/api/v1/admin/utility-bill-imports/${billId}/${path}`)
    saveBlob(blob, path === 'original' ? `utility-bill-${billId}.pdf` : `utility-bill-${billId}-evidence.json`)
  }

  if (accounts.isLoading) return <LoadingState label="Loading utility accounts…" />
  if (accounts.error) return <ErrorState error={accounts.error} retry={() => void accounts.refetch()} />

  return <>
    <PageTitle
      eyebrow="Private administrator workflow"
      title="Import utility bill"
      description="Extract account-specific evidence locally, then review separate rate-plan and billing-cycle drafts before any publication or assignment."
      actions={<Link className="button secondary" to="/rates"><ArrowLeft size={16} /> Rate plans</Link>}
    />

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

    {message && <p className="form-success" role="status">{message}</p>}
    {upload.error && <ErrorState error={upload.error} />}
    {review.error && <ErrorState error={review.error} />}
    {validate.error && <ErrorState error={validate.error} />}
    {publish.error && <ErrorState error={publish.error} />}
    {importCycle.error && <ErrorState error={importCycle.error} />}
    {retention.error && <ErrorState error={retention.error} />}
    {removeOriginal.error && <ErrorState error={removeOriginal.error} />}

    {step === 0 && <Panel title="Select utility account" eyebrow="Step 1 · Account scope">
      {!accounts.data?.length ? <EmptyState title="No utility account" message="Create a utility account before importing its bill." action={<Link className="button primary" to="/admin?tab=sites-accounts">Create utility account</Link>} /> : <>
        <label className="bill-account-select"><span>Utility account</span><select value={accountId} onChange={(event) => { setAccountId(event.target.value); setBillId(''); setSearchParams({ account_id: event.target.value }) }}>{accounts.data.map((account) => <option value={account.id} key={account.id}>{account.site_name} · {account.name}</option>)}</select></label>
        {selectedAccount && <dl className="detail-list bill-account-context"><div><dt>Utility</dt><dd>{selectedAccount.utility_name}</dd></div><div><dt>Provider</dt><dd>{selectedAccount.provider_mode.replaceAll('_', ' ')}</dd></div><div><dt>Timezone</dt><dd>{selectedAccount.timezone}</dd></div><div><dt>Current plan</dt><dd>{selectedAccount.rate_context.current_plan ?? 'Not assigned'}</dd></div></dl>}
      </>}
    </Panel>}

    {step === 1 && <div className="bill-import-columns">
      <Panel title="Upload password-free PDF" eyebrow="Step 2 · Local processing">
        <form className="stack-form" onSubmit={submitUpload}>
          <label><span>Utility-bill PDF</span><input ref={fileInput} type="file" accept="application/pdf,.pdf" required /></label>
          <div className="form-columns">
            <label><span>Source role</span><select value={sourceRole} onChange={(event) => { setSourceRole(event.target.value as SourceRole); }}><option value="supporting">Supporting source (recommended)</option><option value="authoritative_account_specific">Authoritative account-specific source</option><option value="reference_only">Reference only</option></select></label>
            <label><span>Original PDF retention</span><select value={retentionMode} onChange={(event) => { setRetentionMode(event.target.value as RetentionMode); }}><option value="retain">Retain original</option><option value="retain_until">Retain until date</option><option value="delete_after_approval">Delete after approved extraction</option></select></label>
          </div>
          {retentionMode === 'retain_until' && <label><span>Retain until</span><input type="datetime-local" value={retainUntil} onChange={(event) => { setRetainUntil(event.target.value); }} required /></label>}
          <p className="privacy-notice"><ShieldCheck size={18} /><span>Processing stays on this Power Monitor server. The browser does not store document text. A single bill never activates a rate automatically.</span></p>
          <button className="button primary" disabled={!accountId || upload.isPending}><FileUp size={16} /> {upload.isPending ? 'Inspecting and extracting…' : 'Upload and create drafts'}</button>
        </form>
      </Panel>
      <Panel title="Prior imports" eyebrow="Billing-cycle history">
        {history.isLoading ? <LoadingState /> : history.error ? <ErrorState error={history.error} /> : history.data?.length ? <div className="bill-history-list">{history.data.map((item) => <button type="button" key={item.id} onClick={() => { selectBill(item.id); }}><span><strong>{item.utility_account_name}</strong><small>{formatTime(item.created_at)} · {item.extraction_method} · {item.page_count} page{item.page_count === 1 ? '' : 's'}</small></span><StatusPill status={item.status === 'published' ? 'healthy' : item.status === 'ready_to_publish' ? 'pending' : 'failed'} label={item.status.replaceAll('_', ' ')} /></button>)}</div> : <EmptyState title="No uploaded bills" message="The first import for this account will appear here." />}
      </Panel>
    </div>}

    {step === 2 && current && <div className="bill-import-columns">
      <Panel title="Document inspection" eyebrow="Step 3 · Content and parser">
        <dl className="detail-list">
          <div><dt>SHA-256</dt><dd><code title={current.content_sha256}>{current.content_sha256}</code></dd></div>
          <div><dt>Pages</dt><dd>{current.page_count}</dd></div>
          <div><dt>Extraction</dt><dd>{current.extraction_method.replaceAll('_', ' ')}</dd></div>
          <div><dt>Parser</dt><dd>{current.parser_version}</dd></div>
          <div><dt>Automatic activation</dt><dd>Disabled</dd></div>
        </dl>
        <div className="inline-actions">
          {current.original_available && <button className="button secondary" onClick={() => void download('original')}><Download size={15} /> Private original</button>}
          <button className="button secondary" onClick={() => void download('sanitized-evidence')}><Download size={15} /> Sanitized evidence</button>
        </div>
        {[...current.extraction_warnings, ...current.blocking_warnings].map((warning, index) => <p className="billing-warning" key={`${warning.code}-${index}`}><AlertTriangle size={15} /><span>{warning.message ?? warning.code}</span></p>)}
      </Panel>
      <Panel title={`Evidence page ${selectedPage}`} eyebrow="Source excerpts and coordinates" actions={<select aria-label="Evidence page" value={selectedPage} onChange={(event) => { setSelectedPage(Number(event.target.value)); }}>{Array.from({ length: current.page_count }, (_, index) => <option value={index + 1} key={index + 1}>Page {index + 1}</option>)}</select>}>
        {evidence.isLoading ? <LoadingState /> : evidence.error ? <ErrorState error={evidence.error} /> : evidence.data?.fields.length ? <div className="evidence-list">{evidence.data.fields.map((field) => <article key={field.field_key}><header><strong>{field.field_key.replaceAll('_', ' ')}</strong><StatusPill status={confidenceStatus(field.confidence)} label={field.confidence.replaceAll('_', ' ')} /></header><blockquote>{field.source_excerpt || 'No retained excerpt'}</blockquote><small>{field.method} · {field.text_region ? JSON.stringify(field.text_region) : 'coordinates unavailable'}</small></article>)}</div> : <EmptyState title="No fields on this page" message="Other pages may contain the extracted evidence." />}
      </Panel>
    </div>}

    {step === 3 && current && <div className="bill-import-columns">
      <FieldReviewPanel title="Account fields" eyebrow="Step 4 · Account matching" fields={fieldGroups.account} actions={fieldActions} corrections={corrections} onAction={(id, action) => { setFieldActions((value) => ({ ...value, [id]: action })); }} onCorrection={(id, value) => { setCorrections((currentValue) => ({ ...currentValue, [id]: value })); }} />
      <FieldReviewPanel title="Billing-cycle fields" eyebrow="Separate cycle draft" fields={fieldGroups.billing_cycle} actions={fieldActions} corrections={corrections} onAction={(id, action) => { setFieldActions((value) => ({ ...value, [id]: action })); }} onCorrection={(id, value) => { setCorrections((currentValue) => ({ ...currentValue, [id]: value })); }} />
      {current.cycle_draft && <Panel title="Billing-cycle draft" eyebrow="Bill-specific output">
        <dl className="bill-cycle-grid">
          <div><dt>Period</dt><dd>{current.cycle_draft.starts_at && current.cycle_draft.ends_at ? formatBillingPeriod(current.cycle_draft.starts_at, current.cycle_draft.ends_at) : 'Unavailable'}</dd></div>
          <div><dt>Usage</dt><dd>{formatEnergy(current.cycle_draft.total_usage_kwh)}</dd></div>
          <div><dt>Energy subtotal</dt><dd>{formatCurrency(current.cycle_draft.energy_subtotal)}</dd></div>
          <div><dt>Complete bill total</dt><dd>{formatCurrency(current.cycle_draft.full_bill_total)}</dd></div>
          <div><dt>Current / projected tier</dt><dd>{current.cycle_draft.current_tier ?? 'Unavailable'} / {current.cycle_draft.projected_tier ?? 'Unavailable'}</dd></div>
          <div><dt>Import state</dt><dd>{current.cycle_draft.status.replaceAll('_', ' ')}</dd></div>
        </dl>
        <p className="panel-copy">This bill-specific record remains separate from recurring tariff rules. Credits, taxes, and unexplained adjustments are not copied into the rate plan automatically.</p>
      </Panel>}
    </div>}

    {step === 4 && current && <>
      <FieldReviewPanel title="Extracted rate-plan rules" eyebrow="Step 5 · Reusable tariff draft" fields={fieldGroups.rate_plan} actions={fieldActions} corrections={corrections} onAction={(id, action) => { setFieldActions((value) => ({ ...value, [id]: action })); }} onCorrection={(id, value) => { setCorrections((currentValue) => ({ ...currentValue, [id]: value })); }} />
      <Panel title="Tier preview" eyebrow="Structured numeric bounds">
        {current.normalized.rate_plan.tiers?.length ? <div className="responsive-table bill-tier-table"><table><thead><tr><th>Tier</th><th>Range</th><th>Reported usage</th><th>Configured rate</th><th>Reported charge</th></tr></thead><tbody>{current.normalized.rate_plan.tiers.map((tier, index) => <tr key={`${tier.name}-${index}`}><td data-label="Tier">{tier.name ?? `Tier ${index + 1}`}</td><td data-label="Range">{formatTierRange(tier.lower_bound_kwh ?? '0', tier.upper_bound_kwh)}</td><td data-label="Reported usage">{formatEnergy(tier.usage_kwh)}</td><td data-label="Configured rate">{formatEnergyRate(tier.price_per_kwh)}</td><td data-label="Reported charge">{formatCurrency(tier.energy_charge)}</td></tr>)}</tbody></table></div> : <EmptyState title="No complete tier table detected" message="Use the linked custom-rate editor to supply missing tariff rules before validation." />}
        <div className="inline-actions"><Link className="button secondary" to={`/rates/${current.rate_plan_id}/versions/${current.rate_version_id}`}>Edit linked rate draft <ExternalLink size={14} /></Link></div>
      </Panel>
    </>}

    {step === 5 && current && <div className="bill-import-columns">
      <Panel title="Confidence review" eyebrow="Step 6 · Administrator confirmation">
        <div className="confidence-summary">
          {['administrator_confirmed', 'high', 'medium', 'low', 'missing'].map((confidence) => <article key={confidence}><span>{confidence.replaceAll('_', ' ')}</span><strong>{current.fields.filter((field) => field.confidence === confidence).length}</strong></article>)}
        </div>
        <label><span>Threshold interpretation</span><select value={threshold} onChange={(event) => { setThreshold(event.target.value as ThresholdInterpretation); }}><option value="unknown">Unknown — requires administrator review</option><option value="fixed_cycle_threshold">Fixed billing-cycle threshold</option><option value="daily_baseline">Derived from daily baseline</option><option value="baseline_multiplier">Derived from baseline multiplier</option></select></label>
        <label><span>Uploaded bill source role</span><select value={sourceRole} onChange={(event) => { setSourceRole(event.target.value as SourceRole); }}><option value="supporting">Supporting source</option><option value="authoritative_account_specific">Authoritative account-specific source</option><option value="reference_only">Reference only</option></select></label>
        <p className="field-help">A displayed threshold does not prove whether it is fixed, baseline-derived, seasonal, or account-specific.</p>
      </Panel>
      <Panel title="Source conflicts" eyebrow="Current configuration and managed evidence">
        {current.conflicts.length ? <div className="conflict-list">{current.conflicts.map((conflict) => <article key={conflict.id}><header><strong>{conflict.field_key.replaceAll('_', ' ')}</strong><StatusPill status={conflict.status === 'unresolved' ? 'failed' : 'healthy'} label={conflict.status.replaceAll('_', ' ')} /></header><dl><div><dt>Uploaded bill</dt><dd>{reviewValue(conflict.extracted_value)}</dd></div><div><dt>{conflict.comparison_source.replaceAll('_', ' ')}</dt><dd>{reviewValue(conflict.configured_value)}</dd></div></dl>{conflict.status === 'unresolved' && <label><span>Resolution</span><select value={conflictDecisions[conflict.id] ?? ''} onChange={(event) => { setConflictDecisions((value) => ({ ...value, [conflict.id]: event.target.value })); }}><option value="">Review required</option><option value="accepted_bill">Accept uploaded bill value</option><option value="accepted_configured">Keep configured value</option><option value="dismissed">Dismiss with recorded review</option></select></label>}</article>)}</div> : <EmptyState title="No detected conflicts" message="Publication still requires explicit field review and rate-engine validation." />}
      </Panel>
    </div>}

    {step === 6 && current && <Panel title="Rate calculation preview" eyebrow="Step 7 · Exact engine result">
      {comparison.isLoading ? <LoadingState label="Calculating with the linked draft…" /> : comparison.error ? <ErrorState error={comparison.error} /> : !comparison.data?.available ? <EmptyState title="Preview not yet available" message={comparison.data?.reason ?? 'Complete the draft rate rules and cycle fields.'} action={<Link className="button secondary" to={`/rates/${current.rate_plan_id}/versions/${current.rate_version_id}`}>Complete rate draft</Link>} /> : <>
        <section className="bill-comparison-hero">
          <article><span>Calculated energy subtotal</span><strong>{comparison.data.display?.calculated_energy_subtotal}</strong><small>Existing exact rate engine</small></article>
          <article><span>Derived blended rate</span><strong>{comparison.data.display?.blended_energy_rate}</strong><small>Four-decimal display only</small></article>
          <article><span>Utility energy subtotal</span><strong>{comparison.data.display?.utility_energy_subtotal ?? 'Unavailable'}</strong><small>Extracted bill evidence</small></article>
          <article><span>Complete utility bill</span><strong>{comparison.data.display?.utility_full_bill_total ?? 'Unavailable'}</strong><small>Not interchangeable with energy subtotal</small></article>
        </section>
        <dl className="bill-difference-grid"><div><dt>Energy-subtotal difference</dt><dd>{comparison.data.display?.energy_subtotal_difference ?? 'Unavailable'}</dd></div><div><dt>Complete-bill difference</dt><dd>{comparison.data.display?.complete_bill_difference ?? 'Unavailable'}</dd></div><div><dt>Extraction confidence</dt><dd>{comparison.data.extraction_confidence?.replaceAll('_', ' ')}</dd></div><div><dt>Calculation correctness</dt><dd>{comparison.data.calculation_correctness?.replaceAll('_', ' ')}</dd></div></dl>
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
      <Panel title="Validate and publish rate" eyebrow="Step 9 · Explicit immutable change">
        <StatusPill status={current.status === 'ready_to_publish' || current.status === 'published' ? 'healthy' : 'failed'} label={current.status.replaceAll('_', ' ')} />
        <p className="panel-copy">Validation uses the existing rate engine. Publication creates an immutable effective-dated version, then the existing assignment service applies it to {current.utility_account_name}.</p>
        {validate.data && <div className="validation-compact"><strong>{validate.data.validation.valid ? 'Rate-engine validation passed' : 'Validation failed'}</strong>{[...validate.data.validation.errors, ...validate.data.validation.warnings].map((issue) => <span key={`${issue.code}-${issue.path}`}>{issue.message} · {issue.path}</span>)}</div>}
        <div className="inline-actions"><button className="button secondary" disabled={validate.isPending} onClick={() => { validate.mutate(); }}><FileSearch size={15} /> Validate draft</button><button className="button primary" disabled={publish.isPending || current.status !== 'ready_to_publish' || !validate.data?.validation.valid} onClick={() => { if (window.confirm(`Publish and assign this reviewed rate to ${current.utility_account_name}?`)) publish.mutate() }}><CheckCircle2 size={15} /> Publish and assign</button></div>
      </Panel>
      <Panel title="Import billing cycle" eyebrow="Separate bill-specific record">
        <p className="panel-copy">This records exact cycle dates and utility-reported cumulative usage separately. It never overwrites immutable monitored readings.</p>
        <dl className="detail-list"><div><dt>Cycle</dt><dd>{current.cycle_draft?.starts_at && current.cycle_draft.ends_at ? formatBillingPeriod(current.cycle_draft.starts_at, current.cycle_draft.ends_at) : 'Unavailable'}</dd></div><div><dt>Reported usage</dt><dd>{formatEnergy(current.cycle_draft?.total_usage_kwh)}</dd></div><div><dt>Authority</dt><dd>{sourceRole.replaceAll('_', ' ')}</dd></div><div><dt>Status</dt><dd>{current.cycle_draft?.status.replaceAll('_', ' ')}</dd></div></dl>
        <button className="button secondary" disabled={importCycle.isPending || !['ready_to_publish', 'published'].includes(current.status) || current.cycle_draft?.status === 'imported'} onClick={() => { importCycle.mutate(); }}><FileUp size={15} /> Import reviewed billing cycle</button>
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
        <header><div><strong>{field.field_key.replaceAll('.', ' · ').replaceAll('_', ' ')}</strong><small>Page {field.page_number ?? 'unknown'} · {field.extraction_method} · parser {field.parser_version}</small></div><StatusPill status={confidenceStatus(field.confidence)} label={field.confidence.replaceAll('_', ' ')} /></header>
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
