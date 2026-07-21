import { useMutation, useQuery } from '@tanstack/react-query'
import { CheckCircle2, ClipboardCheck, Copy, Download, FileJson, Plus, RefreshCw, Settings2, Upload } from 'lucide-react'
import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { EmptyState, ErrorState, LoadingState, PageTitle, Panel, StatusPill } from '../components/UI'
import type { ManagedRatePlan } from '../rates'

interface CandidateSummary { id: string; status: string; summary: { plan_code?: string; material_differences?: number }; created_at: string }

function downloadJson(filename: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

export function RatesPage({ canManage }: { canManage: boolean }) {
  const navigate = useNavigate()
  const importInput = useRef<HTMLInputElement>(null)
  const [jobId, setJobId] = useState<string>()
  const query = useQuery({ queryKey: ['managed-rates'], queryFn: () => api<ManagedRatePlan[]>('/api/v1/rates/plans') })
  const candidateQuery = useQuery({ queryKey: ['rate-candidates'], queryFn: () => api<CandidateSummary[]>('/api/v1/admin/rate-candidates'), enabled: canManage })
  const check = useMutation({ mutationFn: () => api<{ job_id: string }>('/api/v1/admin/rate-sources/check-now', { method: 'POST' }), onSuccess: (job) => { setJobId(job.job_id) } })
  const syncJob = useQuery({ queryKey: ['rate-sync-job', jobId], queryFn: () => api<{ status: string; progress: { completed?: number; source_ids?: string[] }; result?: { candidate_count?: number } }>(`/api/v1/jobs/${jobId}`), enabled: Boolean(jobId), refetchInterval: (result) => ['queued', 'running'].includes(result.state.data?.status ?? 'queued') ? 1500 : false })
  const clone = useMutation({ mutationFn: (id: string) => api<{ editor_url: string }>(`/api/v1/rates/plans/${id}/clone`, { method: 'POST' }), onSuccess: (result) => navigate(result.editor_url) })
  const activate = useMutation({ mutationFn: (id: string) => api<{ status: string }>(`/api/v1/rates/versions/${id}/activate`, { method: 'POST' }), onSuccess: () => void query.refetch() })
  const pending = candidateQuery.data?.filter((item) => item.status === 'pending_review') ?? []

  async function exportVersion(id: string, code: string) {
    const result = await api<{ document: unknown; integrity_sha256: string }>(`/api/v1/rates/versions/${id}/export`)
    downloadJson(`${code.toLowerCase()}-rate-plan.json`, { ...result.document as object, integrity_sha256: result.integrity_sha256 })
  }

  async function importPlan(file?: File) {
    if (!file) return
    const body = new FormData()
    body.append('upload', file)
    const result = await api<{ plan_id: string; version_id: string }>('/api/v1/rates/import', { method: 'POST', body })
    void navigate(`/rates/${result.plan_id}/versions/${result.version_id}`)
  }

  return (
    <>
      <PageTitle
        eyebrow="Tariff library"
        title="Rate plans"
        description="Effective-dated, source-backed versions preserve historical estimates while new utility changes remain reviewable."
        actions={canManage && <>
          <button className="button secondary" disabled={check.isPending} onClick={() => { check.mutate(); }}><RefreshCw size={16} className={check.isPending ? 'spin' : ''} /> Check SCE now</button>
          <button className="button secondary" onClick={() => navigate('/rates/sources')}><Settings2 size={16} /> Rate source settings</button>
          <button className="button primary" onClick={() => navigate('/rates/new')}><Plus size={17} /> Custom plan</button>
        </>}
      />

      {check.isSuccess && <p className="form-success" role="status">SCE source check {syncJob.data?.status ?? 'queued'} · {syncJob.data?.progress.completed ?? 0}/{syncJob.data?.progress.source_ids?.length ?? 4} sources · {syncJob.data?.result?.candidate_count ?? 0} candidates.</p>}
      {check.error && <ErrorState error={check.error} />}

      {query.isLoading ? <LoadingState /> : query.error ? <ErrorState error={query.error} retry={() => void query.refetch()} /> : query.data?.length ? (
        <div className="rate-grid">
          {query.data.map((plan) => {
            const version = plan.versions.find((item) => item.is_active) ?? plan.versions[0]
            return <Panel key={plan.id} className="rate-card">
              <header className="rate-card-head"><div><span className="plan-code">{plan.code}</span><h2>{plan.name}</h2></div><StatusPill status={version?.status ?? plan.status} label={version?.is_active ? 'Active' : version?.status ?? plan.status} /></header>
              <p>{plan.description || 'No plan description has been provided.'}</p>
              {pending.some((item) => item.summary.plan_code === plan.code) && <StatusPill status="pending" label="Update available" />}
              {version && <>
                <dl className="rate-meta"><div><dt>Effective</dt><dd>{version.effective_from}</dd></div><div><dt>Source checked</dt><dd>{version.source_checked_at?.slice(0, 10) ?? 'Manual'}</dd></div><div><dt>Version</dt><dd>v{version.version}</dd></div><div><dt>Integrity</dt><dd title={version.integrity_sha256}>{version.integrity_sha256.slice(0, 10)}…</dd></div></dl>
                <div className="source-note"><ClipboardCheck size={17} /><p>{version.source_label || (plan.plan_kind === 'custom' ? 'Administrator-defined plan' : 'SCE archived evidence')}</p></div>
                <footer><button className="link-button" onClick={() => navigate(`/rates/${plan.id}/versions/${version.id}`)}>View details</button><div>
                  <button className="button ghost" onClick={() => void exportVersion(version.id, plan.code)}><FileJson size={15} /> Export</button>
                  {canManage && <button className="button secondary" disabled={clone.isPending} onClick={() => { clone.mutate(plan.id); }}><Copy size={15} /> Clone</button>}
                  {canManage && version.status === 'draft' && <button className="button primary" disabled={activate.isPending} onClick={() => { activate.mutate(version.id); }}><CheckCircle2 size={15} /> Activate</button>}
                </div></footer>
              </>}
            </Panel>
          })}
        </div>
      ) : <EmptyState title="No rate plans" message="Run first-time initialization to install effective-dated SCE presets." />}

      <p className="rate-disclaimer">This estimate is not an SCE bill. Rates shown may differ when generation is provided by a CCA or Direct Access provider.</p>

      {canManage && <Panel title="Plan portability" eyebrow="Controlled import and export" actions={<>
        <input ref={importInput} className="sr-only" type="file" accept="application/json,.json" onChange={(event) => void importPlan(event.target.files?.[0])} />
        <button className="button secondary" onClick={() => importInput.current?.click()}><Upload size={15} /> Import plan</button>
        <button className="button ghost" onClick={() => navigate('/rates/sources')}><Download size={15} /> Evidence archive</button>
      </>}><p className="panel-copy">Imports are schema-validated, size-limited, and always created as inactive drafts. Exports contain exact decimal strings and no credentials.</p></Panel>}

      <Panel title="Activation checklist" eyebrow="Before calculating costs"><div className="checklist"><div><span>1</span><p><strong>Verify the plan code</strong><small>Find it on the current SCE bill.</small></p></div><div><span>2</span><p><strong>Review archived evidence</strong><small>Confirm source dates, exact prices, and any conflicts.</small></p></div><div><span>3</span><p><strong>Set the cost scope</strong><small>One-CT devices default to energy-only.</small></p></div><div><span>4</span><p><strong>Configure provider adjustments</strong><small>CCA and Direct Access generation remain explicit.</small></p></div></div></Panel>
    </>
  )
}
