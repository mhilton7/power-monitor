import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, FileCheck2, FileText, ShieldCheck, Upload, X } from 'lucide-react'
import { useRef, useState } from 'react'
import { adaptBillDetail } from '../../api/adapters'
import { errorMessage, json, request } from '../../api/client'
import { InlineNotice, LoadingState } from '../../components/feedback/States'
import type { BillImportDetail, ElectricService, Home } from '../../types/models'
import { dateRange, energy, money, statusLabel } from '../../utils/format'

type Step = 'upload' | 'review' | 'confirm' | 'apply' | 'done'

const steps: Array<{ id: Step; label: string }> = [
  { id: 'upload', label: 'Upload' },
  { id: 'review', label: 'Review' },
  { id: 'confirm', label: 'Confirm' },
  { id: 'apply', label: 'Apply' },
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
  const [applied, setApplied] = useState<{ rate: boolean; cycle: boolean }>({ rate: false, cycle: false })
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
          field_reviews: bill.fields.map((field) => ({ field_id: field.id, action: 'confirm' })),
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
  const validate = useMutation({
    mutationFn: async () => {
      if (!bill) throw new Error('The extracted bill is unavailable.')
      const result = await request<{ validation?: { valid?: boolean }; blocking_warnings?: string[] }>(
        `/api/v1/admin/utility-bill-imports/${bill.id}/validate`,
        json('POST'),
      )
      if (result.validation?.valid === false || (result.blocking_warnings?.length ?? 0) > 0) {
        throw new Error('Resolve the highlighted bill values before applying this plan.')
      }
      return result
    },
    onSuccess: () => {
      setStep('apply')
    },
  })
  const apply = useMutation({
    mutationFn: async () => {
      if (!bill) throw new Error('The extracted bill is unavailable.')
      if (!serviceId) throw new Error('Create an electric service before applying this bill.')
      await request(`/api/v1/admin/utility-bill-imports/${bill.id}/publish-and-assign`, json('POST', {}))
      setApplied((value) => ({ ...value, rate: true }))
      await request(`/api/v1/admin/utility-bill-imports/${bill.id}/import-billing-cycle`, json('POST'))
      setApplied({ rate: true, cycle: true })
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

  const currentIndex = steps.findIndex((item) => item.id === step)
  const error = upload.error ?? review.error ?? validate.error ?? apply.error
  const retry = () => {
    if (step === 'upload') upload.mutate()
    else if (step === 'review') review.mutate()
    else if (step === 'confirm') validate.mutate()
    else if (step === 'apply') apply.mutate()
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
            {!serviceId && <InlineNotice tone="warning">This bill can be reviewed now, but an electric service is required before Apply.</InlineNotice>}
            <div className="workflow-security"><ShieldCheck /><span><strong>Processed locally</strong><small>Text extraction, OCR fallback, validation, and evidence storage remain on this server.</small></span></div>
          </>
        )}
        {step === 'review' && bill && (
          <>
            <div className="review-summary">
              <FileText />
              <div><strong>{dateRange(bill.startsAt, bill.endsAt)}</strong><span>{energy(bill.usageKwh)} · {money(bill.total, home.currency)} · {bill.pageCount} pages</span></div>
              <span className="pill">{statusLabel(bill.extractionMethod ?? 'extracted')}</span>
            </div>
            <div className="review-fields">
              {bill.fields.length ? bill.fields.map((field) => (
                <div key={field.id}>
                  <span>{statusLabel(field.label)}</span>
                  <strong>{field.value ?? 'Unknown'}</strong>
                  <small>{field.sourcePage ? `Page ${field.sourcePage}` : 'Source retained'} · {field.confidence ?? 'unscored'} confidence</small>
                </div>
              )) : <InlineNotice tone="warning">The parser did not return reviewable values. The bill remains a draft.</InlineNotice>}
            </div>
            {bill.conflicts.map((conflict) => <InlineNotice key={conflict.id} tone="warning">{statusLabel(conflict.path)}: {conflict.message}</InlineNotice>)}
            <label><span>Tier threshold meaning</span><select value={threshold} onChange={(event) => { setThreshold(event.target.value as typeof threshold); }}><option value="unknown">Not stated / not applicable</option><option value="fixed_cycle_threshold">Fixed billing-cycle threshold</option><option value="daily_baseline">Daily baseline</option><option value="baseline_multiplier">Baseline multiplier</option></select></label>
          </>
        )}
        {step === 'confirm' && bill && (
          <div className="confirm-card">
            <FileCheck2 />
            <h3>Confirm the reviewed values</h3>
            <p>This creates a separate rate-plan version and billing-cycle draft. Nothing was activated from upload alone.</p>
            <dl>
              <div><dt>Bill period</dt><dd>{dateRange(bill.startsAt, bill.endsAt)}</dd></div>
              <div><dt>Usage</dt><dd>{energy(bill.usageKwh)}</dd></div>
              <div><dt>Total</dt><dd>{money(bill.total, home.currency)}</dd></div>
              <div><dt>Destination</dt><dd>{services.find((item) => item.id === serviceId)?.name ?? 'Electric service required'}</dd></div>
            </dl>
            <label className="check-row"><input type="checkbox" checked={confirmed} onChange={(event) => { setConfirmed(event.target.checked); }} /><span>I reviewed these values and want to continue.</span></label>
          </div>
        )}
        {step === 'apply' && (
          <div className="confirm-card">
            <ShieldCheck />
            <h3>Ready to apply</h3>
            <p>The reviewed plan will become the current plan for this electric service. Existing versions, evidence, and historical assignments remain available.</p>
            {apply.isPending && <LoadingState label={applied.rate ? 'Importing the billing cycle…' : 'Publishing and assigning the reviewed rate…'} />}
          </div>
        )}
        {step === 'done' && (
          <div className="done-state">
            <span><Check /></span>
            <h3>Bill applied</h3>
            <p>Your reviewed rate and billing cycle are now connected to this electric service.</p>
            <button type="button" className="button primary" onClick={onClose}>Return to Billing</button>
          </div>
        )}
        {error && <div className="workflow-error" role="alert"><p>{errorMessage(error)}</p><button type="button" className="button secondary compact" onClick={retry}>Retry this step</button></div>}
      </div>
      {step !== 'done' && (
        <footer className="workflow-footer">
          <button type="button" className="button secondary" onClick={onClose}>Cancel</button>
          {step === 'upload' && <button type="button" className="button primary" disabled={!file || upload.isPending} onClick={() => { upload.mutate(); }}>{upload.isPending ? 'Extracting…' : 'Upload and review'}</button>}
          {step === 'review' && <button type="button" className="button primary" disabled={review.isPending || bill?.fields.length === 0} onClick={() => { review.mutate(); }}>{review.isPending ? 'Saving review…' : 'Confirm extracted values'}</button>}
          {step === 'confirm' && <button type="button" className="button primary" disabled={!confirmed || validate.isPending} onClick={() => { validate.mutate(); }}>{validate.isPending ? 'Validating…' : 'Continue to Apply'}</button>}
          {step === 'apply' && <button type="button" className="button primary" disabled={apply.isPending || !serviceId} onClick={() => { apply.mutate(); }}>{apply.isPending ? 'Applying…' : 'Apply plan and billing cycle'}</button>}
        </footer>
      )}
    </section>
  )
}
