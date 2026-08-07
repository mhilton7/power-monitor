import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, FileCheck2, FileText, ShieldCheck, Upload, X } from 'lucide-react'
import { useRef, useState } from 'react'
import { adaptBillDetail } from '../../api/adapters'
import { errorMessage, json, request } from '../../api/client'
import { record } from '../../api/validation'
import { InlineNotice, LoadingState } from '../../components/feedback/States'
import type { BillImportDetail, ElectricService, Home } from '../../types/models'
import { dateRange, dateTime, energy, money, statusLabel } from '../../utils/format'

type Step = 'upload' | 'review' | 'confirm' | 'done'

const steps: Array<{ id: Step; label: string }> = [
  { id: 'upload', label: 'Upload' },
  { id: 'review', label: 'Review rate rules' },
  { id: 'confirm', label: 'Confirm' },
  { id: 'done', label: 'Done' },
]

export function BillImportFlow({
  home,
  services,
  onClose,
}: {
  home: Home
  services: ElectricService[]
  onClose: () => void
}) {
  const [step, setStep] = useState<Step>('upload')
  const [file, setFile] = useState<File>()
  const [serviceId, setServiceId] = useState(services[0]?.id ?? '')
  const [bill, setBill] = useState<BillImportDetail>()
  const [threshold, setThreshold] = useState<'fixed_cycle_threshold' | 'daily_baseline' | 'baseline_multiplier' | 'unknown'>('unknown')
  const [confirmed, setConfirmed] = useState(false)
  const [cycleDatesApplied, setCycleDatesApplied] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const client = useQueryClient()

  const upload = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error('Choose a PDF bill first.')
      const body = new FormData()
      body.append('upload', file)
      const query = new URLSearchParams({
        timezone: home.timezone,
        currency: home.currency,
        retention_mode: 'retain',
        source_role: 'supporting',
      })
      if (serviceId) query.set('account_id', serviceId)
      return request(`/api/v1/admin/utility-bill-imports?${query.toString()}`, { method: 'POST', body }, adaptBillDetail)
    },
    onSuccess: (data) => {
      setBill(data)
      setThreshold(data.thresholdInterpretation)
      setStep('review')
    },
  })
  const review = useMutation({
    mutationFn: () => {
      if (!bill) throw new Error('The extracted bill is unavailable.')
      return request(
        `/api/v1/admin/utility-bill-imports/${bill.id}/review`,
        json('PUT', {
          revision: bill.revision,
          field_reviews: bill.fields
            .filter((field) => field.calculationRole === 'tariff_rule')
            .map((field) => ({ field_id: field.id, action: 'confirm' })),
          conflict_resolutions: bill.conflicts.map((conflict) => ({
            conflict_id: conflict.id,
            decision: 'accepted_bill',
            note: 'Confirmed in Single Home bill review',
          })),
          threshold_interpretation: threshold,
          source_role: 'supporting',
        }),
        adaptBillDetail,
      )
    },
    onSuccess: (data) => {
      setBill(data)
      setStep('confirm')
    },
  })
  const saveRateRules = useMutation({
    mutationFn: async () => {
      if (!bill) throw new Error('The extracted bill is unavailable.')
      if (!serviceId) throw new Error('Create an electric service before saving these rate rules.')
      const result = await request<{ validation?: { valid?: boolean }; blocking_warnings?: string[] }>(
        `/api/v1/admin/utility-bill-imports/${bill.id}/validate`,
        json('POST'),
      )
      if (result.validation?.valid === false || (result.blocking_warnings?.length ?? 0) > 0) {
        throw new Error('Resolve the highlighted rate-rule values before saving this plan.')
      }
      await request(`/api/v1/admin/utility-bill-imports/${bill.id}/publish-and-assign`, json('POST', {}))
      return result
    },
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['electric-services'] }),
        client.invalidateQueries({ queryKey: ['billing-cycle-summary'] }),
        client.invalidateQueries({ queryKey: ['bills'] }),
        client.invalidateQueries({ queryKey: ['home-summary'] }),
      ])
      setStep('done')
    },
  })
  const applyCycleDates = useMutation({
    mutationFn: async () => {
      if (!bill) throw new Error('The extracted bill is unavailable.')
      return request(
        `/api/v1/admin/utility-bill-imports/${bill.id}/apply-cycle-dates`,
        json('POST'),
      )
    },
    onSuccess: async () => {
      setCycleDatesApplied(true)
      await Promise.all([
        client.invalidateQueries({ queryKey: ['billing-cycle-summary'] }),
        client.invalidateQueries({ queryKey: ['home-summary'] }),
      ])
    },
  })
  const reprocess = useMutation({
    mutationFn: () => {
      if (!bill) throw new Error('The extracted bill is unavailable.')
      return request(
        `/api/v1/admin/utility-bill-imports/${bill.id}/reprocess`,
        json('POST'),
        (value) => adaptBillDetail(record(value, 'reprocess result').bill),
      )
    },
    onSuccess: (data) => {
      setBill(data)
      setThreshold(data.thresholdInterpretation)
    },
  })

  const currentIndex = steps.findIndex((item) => item.id === step)
  const error = upload.error
    ?? review.error
    ?? saveRateRules.error
    ?? applyCycleDates.error
    ?? reprocess.error
  const requiredMissing = bill?.missingFields.filter(
    (field) => field.required && field.calculationRole === 'tariff_rule',
  ) ?? []
  const retry = () => {
    if (step === 'upload') upload.mutate()
    else if (step === 'review') review.mutate()
    else if (step === 'confirm') saveRateRules.mutate()
  }
  return (
    <section className="workflow" role="dialog" aria-modal="true" aria-labelledby="bill-flow-title">
      <header className="workflow-header">
        <div><p>Secure bill import</p><h2 id="bill-flow-title">Upload electric bill</h2></div>
        <button type="button" className="icon-button" aria-label="Close bill import" onClick={onClose}><X /></button>
      </header>
      <ol className="stepper" aria-label="Bill import progress">
        {steps.map((item, index) => (
          <li key={item.id} className={index === currentIndex ? 'active' : index < currentIndex ? 'complete' : ''} aria-current={index === currentIndex ? 'step' : undefined}>
            <span>{index < currentIndex ? <Check /> : index + 1}</span>{item.label}
          </li>
        ))}
      </ol>
      <div className="workflow-body">
        {step === 'upload' && (
          <>
            <InlineNotice tone="info">
              <strong>Rates from your bill; usage from your sensors.</strong> We use this
              document to find energy prices, tier thresholds, schedules, and plan details.
              Its reported kWh and total amount are never used to calculate monitored usage,
              tier progress, or projected energy cost.
            </InlineNotice>
            <div className="drop-zone" onClick={() => inputRef.current?.click()}>
              <Upload aria-hidden="true" />
              <strong>{file?.name ?? 'Choose your electric bill'}</strong>
              <span>PDF only, up to the server’s configured secure limit</span>
              <button type="button" className="button secondary" onClick={(event) => { event.stopPropagation(); inputRef.current?.click() }}>Browse files</button>
              <input ref={inputRef} type="file" accept="application/pdf,.pdf" onChange={(event) => { setFile(event.target.files?.[0]); }} />
            </div>
            {services.length > 1 && (
              <label><span>Electric service</span><select value={serviceId} onChange={(event) => { setServiceId(event.target.value); }}>{services.map((service) => <option key={service.id} value={service.id}>{service.name}</option>)}</select></label>
            )}
            {!serviceId && <InlineNotice tone="warning">This bill can be reviewed now, but an electric service is required before its rate rules can be saved.</InlineNotice>}
            <div className="workflow-security"><ShieldCheck /><span><strong>Processed locally</strong><small>Text extraction, OCR fallback, validation, and evidence storage remain on this server.</small></span></div>
          </>
        )}
        {step === 'review' && bill && (
          <>
            <div className="review-summary">
              <FileText />
              <div>
                <strong>{bill.displayFilename}</strong>
                <span>{bill.utilityName ?? 'Utility not identified'} · {statusLabel(bill.documentType ?? 'electric bill')}</span>
                <small>{bill.pageCount} pages · {statusLabel(bill.extractionMethod ?? 'text')} extraction · imported {dateTime(bill.importedAt)}</small>
              </div>
              <span className="pill">{statusLabel(bill.processingStatus)}</span>
            </div>
            <InlineNotice tone="info">
              Review only the reusable tariff rules below. Power Monitor sensor readings
              remain the exclusive normal usage source.
            </InlineNotice>
            {bill.fields.some((field) => field.calculationRole === 'tariff_rule')
              ? <div className="review-groups">{groupBillFields(bill, 'tariff_rule').map((group) => (
                <section key={group.label} className="review-group">
                  <h3>{group.label}</h3>
                  <div className="review-fields">{group.fields.map((field) => (
                    <div key={field.id}>
                      <span>{statusLabel(field.label)}</span>
                      <strong>{field.value}</strong>
                      <small>{field.sourcePage ? `Page ${field.sourcePage}` : 'Source retained'} · {statusLabel(field.confidence)}</small>
                    </div>
                  ))}</div>
                </section>
              ))}</div>
              : <InlineNotice tone="warning">Unsupported bill layout. No recognized rate rules can be saved.</InlineNotice>}
            {requiredMissing.length > 0 && (
              <section className="missing-fields needs-review" aria-label="Needs review">
                <h3>Needs review</h3>
                <p>These required tariff values were not found and must be corrected before saving.</p>
                <ul>{requiredMissing.map((field) => <li key={`${field.outputKind}-${field.path}`}><strong>{statusLabel(field.path)}</strong><span>{field.reason}</span></li>)}</ul>
              </section>
            )}
            {bill.missingFields.some((field) => !field.required && field.calculationRole === 'tariff_rule') && (
              <details className="missing-fields">
                <summary>Optional rate fields not found ({bill.missingFields.filter((field) => !field.required && field.calculationRole === 'tariff_rule').length})</summary>
                <ul>{bill.missingFields.filter((field) => !field.required && field.calculationRole === 'tariff_rule').map((field) => <li key={`${field.outputKind}-${field.path}`}><strong>{statusLabel(field.path)}</strong><span>{field.state === 'not_applicable' ? 'Not applicable' : field.reason}</span></li>)}</ul>
              </details>
            )}
            {bill.conflicts.map((conflict) => <InlineNotice key={conflict.id} tone="warning"><strong>Existing setup differs — {statusLabel(conflict.path)}.</strong> {conflict.message}</InlineNotice>)}
            <label><span>Tier threshold meaning</span><select value={threshold} onChange={(event) => { setThreshold(event.target.value as typeof threshold); }}><option value="unknown">Not stated / not applicable</option><option value="fixed_cycle_threshold">Fixed billing-cycle threshold</option><option value="daily_baseline">Daily baseline</option><option value="baseline_multiplier">Baseline multiplier</option></select></label>
            <div className="bill-diagnostics" aria-label="Bill diagnostics">
              <button type="button" className="button secondary compact" disabled={reprocess.isPending} onClick={() => { reprocess.mutate() }}>{reprocess.isPending ? 'Reprocessing…' : 'Reprocess bill'}</button>
              <a className="button ghost compact" href={`/api/v1/admin/utility-bill-imports/${bill.id}/evidence/pages/1`} target="_blank" rel="noreferrer">View evidence</a>
              <a className="button ghost compact" href={`/api/v1/admin/utility-bill-imports/${bill.id}/extracted-text`} target="_blank" rel="noreferrer">View extracted text</a>
              <a className="button ghost compact" href={`/api/v1/admin/utility-bill-imports/${bill.id}/normalized`} target="_blank" rel="noreferrer">View normalized data</a>
              <a className="button ghost compact" href={`/api/v1/admin/utility-bill-imports/${bill.id}/sanitized-evidence`} download>Download normalized JSON</a>
            </div>
            <details className="bill-reference-details">
              <summary>
                Reference information from uploaded bill
                <span className="pill muted">Not used in calculation</span>
              </summary>
              <p>
                These bill-specific values are retained as evidence only. They do not seed
                monitored usage, tier progress, projections, or energy-cost estimates.
              </p>
              <div className="review-groups">{groupBillFields(bill, 'reference_only').map((group) => (
                <section key={group.label} className="review-group">
                  <h3>{group.label}</h3>
                  <div className="review-fields">{group.fields.map((field) => (
                    <div key={field.id}>
                      <span>{statusLabel(field.label)}</span>
                      <strong>{field.value}</strong>
                      <small>Reference only · {field.sourcePage ? `Page ${field.sourcePage}` : 'Source retained'}</small>
                    </div>
                  ))}</div>
                </section>
              ))}</div>
            </details>
          </>
        )}
        {step === 'confirm' && bill && (
          <div className="confirm-card">
            <FileCheck2 />
            <h3>Save reviewed rate rules</h3>
            <p>
              This creates and assigns a reviewed rate-plan version. Usage will continue
              to come from Power Monitor sensors.
            </p>
            <div className="bill-boundary-summary">
              <section>
                <h4>Will import</h4>
                <ul>
                  {bill.fields
                    .filter((field) => field.calculationRole === 'tariff_rule')
                    .slice(0, 8)
                    .map((field) => <li key={field.id}>{statusLabel(field.label)}: {field.value}</li>)}
                </ul>
              </section>
              <section>
                <h4>Will not import</h4>
                <ul>
                  <li>{energy(bill.usageKwh)} reported usage</li>
                  <li>{money(bill.total, home.currency)} bill total</li>
                  <li>Bill tier allocation or current tier</li>
                  <li>Taxes, credits, balances, or projection seeds</li>
                </ul>
              </section>
            </div>
            <p>
              Destination: <strong>{services.find((item) => item.id === serviceId)?.name ?? 'Electric service required'}</strong>
            </p>
            <label className="check-row"><input type="checkbox" checked={confirmed} onChange={(event) => { setConfirmed(event.target.checked); }} /><span>I reviewed these rate rules and understand that bill usage is reference only.</span></label>
            {saveRateRules.isPending && <LoadingState label="Saving and assigning the reviewed rate rules…" />}
          </div>
        )}
        {step === 'done' && (
          <div className="done-state">
            <span><Check /></span>
            <h3>Rate rules saved</h3>
            <p>
              The reviewed tariff is connected to this electric service. Bill-reported
              usage and totals remain reference evidence and did not change calculations.
            </p>
            {bill?.startsAt && bill.endsAt && !cycleDatesApplied && (
              <div className="optional-cycle-dates">
                <strong>Optional: apply billing-cycle dates only</strong>
                <span>{dateRange(bill.startsAt, bill.endsAt)}</span>
                <small>This updates start and end dates. It does not import bill kWh.</small>
                <button
                  type="button"
                  className="button secondary"
                  disabled={applyCycleDates.isPending}
                  onClick={() => { applyCycleDates.mutate() }}
                >
                  {applyCycleDates.isPending ? 'Applying dates…' : 'Apply cycle dates only'}
                </button>
              </div>
            )}
            {cycleDatesApplied && <InlineNotice tone="success">Cycle dates applied. Sensor usage remains unchanged.</InlineNotice>}
            <button type="button" className="button primary" onClick={onClose}>Return to Billing</button>
          </div>
        )}
        {error && <div className="workflow-error" role="alert"><p>{errorMessage(error)}</p><button type="button" className="button secondary compact" onClick={retry}>Retry this step</button></div>}
      </div>
      {step !== 'done' && (
        <footer className="workflow-footer">
          <button type="button" className="button secondary" onClick={onClose}>Cancel</button>
          {step === 'upload' && <button type="button" className="button primary" disabled={!file || upload.isPending} onClick={() => { upload.mutate(); }}>{upload.isPending ? 'Extracting…' : 'Upload and review'}</button>}
          {step === 'review' && <button type="button" className="button primary" disabled={review.isPending || !bill?.fields.some((field) => field.calculationRole === 'tariff_rule') || requiredMissing.length > 0} onClick={() => { review.mutate(); }}>{review.isPending ? 'Saving review…' : 'Review rate rules'}</button>}
          {step === 'confirm' && <button type="button" className="button primary" disabled={!confirmed || saveRateRules.isPending || !serviceId} onClick={() => { saveRateRules.mutate(); }}>{saveRateRules.isPending ? 'Saving…' : 'Save rate rules'}</button>}
        </footer>
      )}
    </section>
  )
}

const reviewGroupOrder = [
  'Bill summary',
  'Billing cycle',
  'Usage',
  'Rate plan',
  'Charges and taxes',
  'Credits and adjustments',
  'Validation',
] as const

function reviewGroup(path: string): typeof reviewGroupOrder[number] {
  if (path.includes('validation') || path.includes('subtotal') || path.includes('total_new')) return 'Validation'
  if (path.includes('credit') || path.includes('adjustment')) return 'Credits and adjustments'
  if (path.includes('charge') || path.includes('tax') || path.startsWith('line_items.')) return 'Charges and taxes'
  if (path.includes('rate') || path.includes('pricing') || path.includes('season') || path.includes('baseline')) return 'Rate plan'
  if (path.includes('usage') || path.includes('meter')) return 'Usage'
  if (path.includes('period') || path.includes('cycle') || path.includes('starts_at') || path.includes('ends_at')) return 'Billing cycle'
  return 'Bill summary'
}

function groupBillFields(
  bill: BillImportDetail,
  calculationRole: 'tariff_rule' | 'reference_only',
) {
  return reviewGroupOrder
    .map((label) => ({
      label,
      fields: bill.fields.filter(
        (field) => field.calculationRole === calculationRole && reviewGroup(field.path) === label,
      ),
    }))
    .filter((group) => group.fields.length > 0)
}
