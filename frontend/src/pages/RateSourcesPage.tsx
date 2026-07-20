import { useMutation, useQuery } from '@tanstack/react-query'
import {
  Archive,
  CheckCircle2,
  Clock3,
  ExternalLink,
  FileUp,
  Plus,
  RefreshCw,
  Settings2,
  ShieldCheck,
  XCircle,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageTitle,
  Panel,
  StatusPill,
  formatTime,
} from '../components/UI'

interface Configuration {
  enabled: boolean
  schedule_cron: string
  timezone: string
  jitter_minutes: number
  approval_mode: string
  auto_activate_verified: boolean
  next_scheduled_run?: string
}

type EditableConfiguration = Pick<
  Configuration,
  | 'enabled'
  | 'schedule_cron'
  | 'timezone'
  | 'jitter_minutes'
  | 'approval_mode'
  | 'auto_activate_verified'
>

interface SettingsUpdateResponse {
  updated: boolean
  configuration: Configuration
}

interface SourceSettings {
  configuration: Configuration
  last_successful_check?: string
  sources: Array<{
    id: string
    name: string
    url: string
    parser_id: string
    effective_from?: string
    enabled: boolean
    last_success_at?: string
    consecutive_failures: number
  }>
}

interface SourceDraft {
  name: string
  url: string
  parser_id: string
  effective_from: string
}

const parserOptions = [
  {
    value: 'sce_public_tou_html_v1',
    label: 'SCE TOU rate page',
    help: 'Extracts published TOU schedules and prices into review candidates.',
  },
  {
    value: 'sce_rate_advisory_html_v1',
    label: 'SCE rate advisory',
    help: 'Archives official change notices and structured advisory data.',
  },
  {
    value: 'sce_tariff_index_html_v1',
    label: 'SCE tariff index',
    help: 'Archives an index page and records approved tariff PDF links.',
  },
  {
    value: 'sce_tariff_pdf_v1',
    label: 'SCE tariff PDF',
    help: 'Archives a direct official PDF for evidence and supported extraction.',
  },
] as const

const emptySourceDraft = (): SourceDraft => ({
  name: '',
  url: '',
  parser_id: 'sce_public_tou_html_v1',
  effective_from: '',
})

interface Candidate {
  id: string
  status: string
  summary: { plan_code?: string; material_differences?: number }
  created_at: string
}

interface CandidateDetail extends Candidate {
  differences: Array<{
    path: string
    change_type: string
    before: unknown
    after: unknown
    material: boolean
  }>
  source_evidence?: {
    artifact_id: string
    sha256: string
    captured_at: string
    parser_id: string
    parser_version: string
    warnings: Array<{ code?: string; message?: string }>
  }
}

interface SyncJob {
  id: string
  status: string
  progress: { completed?: number; source_ids?: string[] }
  result?: { candidate_count?: number }
  error?: { detail?: string }
}

interface Check {
  id: string
  rate_source_id: string
  checked_at: string
  outcome: string
  http_status?: number
  error_code?: string
}

export function RateSourcesPage() {
  const navigate = useNavigate()
  const [selected, setSelected] = useState<string>()
  const [rejectReason, setRejectReason] = useState('')
  const [draftSettings, setDraftSettings] = useState<Configuration>()
  const [jobId, setJobId] = useState<string>()
  const [showSourceForm, setShowSourceForm] = useState(false)
  const [sourceDraft, setSourceDraft] = useState<SourceDraft>(emptySourceDraft)
  const sourceQuery = useQuery({
    queryKey: ['rate-sources'],
    queryFn: () => api<SourceSettings>('/api/v1/admin/rate-sources'),
  })
  const candidates = useQuery({
    queryKey: ['rate-candidates'],
    queryFn: () => api<Candidate[]>('/api/v1/admin/rate-candidates'),
  })
  const checks = useQuery({
    queryKey: ['rate-checks'],
    queryFn: () => api<Check[]>('/api/v1/admin/rate-checks'),
  })
  const detail = useQuery({
    queryKey: ['rate-candidate', selected],
    queryFn: () => api<CandidateDetail>(`/api/v1/admin/rate-candidates/${selected}`),
    enabled: Boolean(selected),
  })
  const checkAll = useMutation({
    mutationFn: () => api<{ job_id: string }>('/api/v1/admin/rate-sources/check-now', { method: 'POST' }),
    onSuccess: (job) => { setJobId(job.job_id) },
  })
  const syncJob = useQuery({
    queryKey: ['rate-sync-job', jobId],
    queryFn: () => api<SyncJob>(`/api/v1/jobs/${jobId}`),
    enabled: Boolean(jobId),
    refetchInterval: (query) => ['queued', 'running'].includes(query.state.data?.status ?? 'queued') ? 1500 : false,
  })
  const checkOne = useMutation({
    mutationFn: (id: string) =>
      api<{ job_id: string }>(`/api/v1/admin/rate-sources/${id}/check`, { method: 'POST' }),
    onSuccess: (job) => { setJobId(job.job_id) },
  })
  const createSource = useMutation({
    mutationFn: (draft: SourceDraft) => api('/api/v1/admin/rate-sources', {
      method: 'POST',
      body: JSON.stringify({
        name: draft.name.trim(),
        url: draft.url.trim(),
        parser_id: draft.parser_id,
        effective_from: draft.effective_from || undefined,
      }),
    }),
    onSuccess: async () => {
      setShowSourceForm(false)
      setSourceDraft(emptySourceDraft())
      await sourceQuery.refetch()
    },
  })
  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api(`/api/v1/admin/rate-sources/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ enabled }),
      }),
    onSuccess: () => {
      void sourceQuery.refetch()
    },
  })
  const saveSettings = useMutation({
    mutationFn: (configuration: Configuration) => {
      const payload: EditableConfiguration = {
        enabled: configuration.enabled,
        schedule_cron: configuration.schedule_cron,
        timezone: configuration.timezone,
        jitter_minutes: configuration.jitter_minutes,
        approval_mode: configuration.approval_mode,
        auto_activate_verified: configuration.auto_activate_verified,
      }
      return api<SettingsUpdateResponse>('/api/v1/admin/rate-source-settings', {
        method: 'PATCH',
        body: JSON.stringify(payload),
      })
    },
    onSuccess: async (result) => {
      setDraftSettings({ ...result.configuration })
      await sourceQuery.refetch()
    },
  })
  const decide = useMutation({
    mutationFn: ({ id, action, comment }: { id: string; action: 'approve' | 'reject'; comment: string }) =>
      api(`/api/v1/admin/rate-candidates/${id}/${action}`, {
        method: 'POST',
        body: JSON.stringify({ comment }),
      }),
    onSuccess: () => {
      void candidates.refetch()
      void detail.refetch()
    },
  })
  const activate = useMutation({
    mutationFn: (id: string) =>
      api(`/api/v1/admin/rate-candidates/${id}/activate`, { method: 'POST' }),
    onSuccess: () => {
      void candidates.refetch()
      void detail.refetch()
    },
  })

  useEffect(() => {
    if (sourceQuery.data?.configuration) {
      setDraftSettings({ ...sourceQuery.data.configuration })
    }
  }, [sourceQuery.data?.configuration])

  async function uploadEvidence(sourceId: string, file?: File) {
    if (!file) return
    const body = new FormData()
    body.append('upload', file)
    await api(
      `/api/v1/admin/rate-artifacts/upload?source_id=${encodeURIComponent(sourceId)}`,
      { method: 'POST', body },
    )
    await checks.refetch()
  }

  if (sourceQuery.isLoading) return <LoadingState label="Opening rate source controls…" />
  if (sourceQuery.error) {
    return <ErrorState error={sourceQuery.error} retry={() => void sourceQuery.refetch()} />
  }

  return (
    <>
      <PageTitle
        eyebrow="Tariff evidence"
        title="SCE rate sources"
        description="Approved sources are fetched, hashed, archived, parsed, and compared. No candidate changes an active rate without the configured approval workflow."
        actions={
          <>
            <button className="button secondary" onClick={() => navigate('/rates')}>
              Rate plans
            </button>
            <button
              className="button primary"
              disabled={checkAll.isPending}
              onClick={() => { checkAll.mutate(); }}
            >
              <RefreshCw size={16} className={checkAll.isPending ? 'spin' : ''} /> Check SCE now
            </button>
          </>
        }
      />
      <div className="source-health-grid">
        <article><ShieldCheck /><span>Last successful check<strong>{formatTime(sourceQuery.data?.last_successful_check)}</strong></span></article>
        <article><Clock3 /><span>Next scheduled check<strong>{formatTime(draftSettings?.next_scheduled_run)}</strong><small>{draftSettings?.schedule_cron} · {draftSettings?.timezone}</small></span></article>
        <article><Archive /><span>Review policy<strong>{draftSettings?.approval_mode.replaceAll('_', ' ')}</strong></span></article>
      </div>
      {jobId && <div className="job-progress" role="status"><RefreshCw className={['queued', 'running'].includes(syncJob.data?.status ?? 'queued') ? 'spin' : ''} /><div><strong>SCE check {syncJob.data?.status ?? 'queued'}</strong><small>{syncJob.data?.progress.completed ?? 0} of {syncJob.data?.progress.source_ids?.length ?? 4} sources · {syncJob.data?.result?.candidate_count ?? 0} candidates</small>{syncJob.data?.error?.detail && <small>{syncJob.data.error.detail}</small>}</div></div>}

      {draftSettings && (
        <Panel title="Synchronization settings" eyebrow="Database overrides deployment defaults">
          <form
            className="source-settings-form"
            onChange={() => { saveSettings.reset() }}
            onSubmit={(event) => {
              event.preventDefault()
              saveSettings.mutate(draftSettings)
            }}
          >
            <label className="switch-row">
              <span><strong>Weekly synchronization</strong><small>Run the approved-source check on schedule.</small></span>
              <span className="switch">
                <input
                  type="checkbox"
                  checked={draftSettings.enabled}
                  onChange={(event) =>
                    { setDraftSettings({ ...draftSettings, enabled: event.target.checked }); }}
                />
                <span />
              </span>
            </label>
            <div className="form-columns">
              <label>
                Cron schedule
                <input
                  value={draftSettings.schedule_cron}
                  onChange={(event) =>
                    { setDraftSettings({ ...draftSettings, schedule_cron: event.target.value }); }}
                />
                <small>Default: Sunday at 03:15 (`15 3 * * 0`).</small>
              </label>
              <label>
                Timezone
                <input
                  value={draftSettings.timezone}
                  onChange={(event) =>
                    { setDraftSettings({ ...draftSettings, timezone: event.target.value }); }}
                />
              </label>
            </div>
            {draftSettings.approval_mode === 'auto_activate_verified' && <label className="switch-row auto-activation-warning">
              <span><strong>Enable strict automatic activation</strong><small>Only archived, warning-free, allowlisted evidence below every configured safety threshold can activate.</small></span>
              <span className="switch"><input aria-label="Enable strict automatic activation" type="checkbox" checked={draftSettings.auto_activate_verified} onChange={(event) => { setDraftSettings({ ...draftSettings, auto_activate_verified: event.target.checked }); }} /><span /></span>
            </label>}
            <div className="form-columns">
              <label>
                Jitter minutes
                <input
                  type="number"
                  min="0"
                  max="20"
                  value={draftSettings.jitter_minutes}
                  onChange={(event) => { setDraftSettings({
                    ...draftSettings,
                    jitter_minutes: Number(event.target.value),
                  }); }}
                />
              </label>
              <label>
                Activation policy
                <select
                  value={draftSettings.approval_mode}
                  onChange={(event) => { setDraftSettings({
                    ...draftSettings,
                    approval_mode: event.target.value,
                  }); }}
                >
                  <option value="manual_review">Manual review</option>
                  <option value="notify_only">Notify only</option>
                  <option value="auto_activate_verified">Auto-activate verified (strict)</option>
                </select>
              </label>
            </div>
            <footer>
              <p><ShieldCheck size={15} /> Managed sources remain restricted to approved HTTPS SCE paths.</p>
              <button className="button primary" type="submit" disabled={saveSettings.isPending}>
                <Settings2 size={15} className={saveSettings.isPending ? 'spin' : undefined} /> {saveSettings.isPending ? 'Saving settings…' : 'Save settings'}
              </button>
            </footer>
            {saveSettings.isSuccess && <p className="form-success" role="status"><CheckCircle2 size={16} /> Rate source settings saved.</p>}
            {saveSettings.error && <p className="field-error" role="alert">{saveSettings.error.message}</p>}
          </form>
        </Panel>
      )}

      <Panel
        title="Approved source status"
        eyebrow="HTTPS SCE allowlist"
        actions={<button className="button secondary" type="button" aria-expanded={showSourceForm} onClick={() => { setShowSourceForm((value) => !value); createSource.reset(); }}><Plus size={15} /> Add source</button>}
      >
        {showSourceForm && (
          <form
            className="source-create-form"
            onChange={() => { createSource.reset() }}
            onSubmit={(event) => { event.preventDefault(); createSource.mutate(sourceDraft) }}
          >
            <div className="form-columns">
              <label>
                Source name
                <input value={sourceDraft.name} minLength={3} maxLength={160} required placeholder="SCE residential TOU page" onChange={(event) => { setSourceDraft({ ...sourceDraft, name: event.target.value }) }} />
              </label>
              <label>
                Source type
                <select value={sourceDraft.parser_id} onChange={(event) => { setSourceDraft({ ...sourceDraft, parser_id: event.target.value }) }}>
                  {parserOptions.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
                </select>
                <small>{parserOptions.find((option) => option.value === sourceDraft.parser_id)?.help}</small>
              </label>
            </div>
            <label>
              Official SCE HTTPS URL
              <input type="url" value={sourceDraft.url} required placeholder="https://www.sce.com/save-money/rates-financing/..." onChange={(event) => { setSourceDraft({ ...sourceDraft, url: event.target.value }) }} />
              <small>Only approved sce.com rate and tariff paths are accepted. Private hosts, credentials, redirects to other domains, and nonstandard ports are blocked.</small>
            </label>
            <label>
              Effective date {sourceDraft.parser_id === 'sce_public_tou_html_v1' ? '(required)' : '(optional)'}
              <input type="date" value={sourceDraft.effective_from} required={sourceDraft.parser_id === 'sce_public_tou_html_v1'} onChange={(event) => { setSourceDraft({ ...sourceDraft, effective_from: event.target.value }) }} />
              <small>Use the date stated by the supporting SCE advisory or filed tariff. It is never inferred from the page retrieval date.</small>
            </label>
            {createSource.error && <p className="field-error" role="alert">{createSource.error.message}</p>}
            <footer>
              <button className="button ghost" type="button" onClick={() => { setShowSourceForm(false); createSource.reset() }}>Cancel</button>
              <button className="button primary" type="submit" disabled={createSource.isPending}><Plus size={15} /> {createSource.isPending ? 'Adding source…' : 'Add approved source'}</button>
            </footer>
          </form>
        )}
        {createSource.isSuccess && <p className="form-success" role="status"><CheckCircle2 size={16} /> Source added. Run its check to create review candidates.</p>}
        {sourceQuery.data?.sources.length ? <div className="source-list">
          {sourceQuery.data.sources.map((source) => (
            <article key={source.id}>
              <span className="source-icon"><ExternalLink size={17} /></span>
              <div>
                <strong>{source.name}</strong>
                <a href={source.url} target="_blank" rel="noreferrer">{source.url}</a>
                <small>{source.parser_id}{source.effective_from ? ` · Effective ${source.effective_from}` : ''} · Last success {formatTime(source.last_success_at)}</small>
              </div>
              <StatusPill
                status={source.consecutive_failures ? 'failed' : source.last_success_at ? 'healthy' : 'pending'}
                label={source.consecutive_failures ? `${source.consecutive_failures} failures` : source.last_success_at ? 'Healthy' : 'Not checked'}
              />
              <label className="switch" title="Enable source">
                <input
                  type="checkbox"
                  checked={source.enabled}
                  onChange={(event) => { toggle.mutate({ id: source.id, enabled: event.target.checked }); }}
                />
                <span />
              </label>
              <button className="button ghost" disabled={checkOne.isPending} onClick={() => { checkOne.mutate(source.id); }}>
                <RefreshCw size={14} className={checkOne.isPending && checkOne.variables === source.id ? 'spin' : undefined} /> {checkOne.isPending && checkOne.variables === source.id ? 'Checking…' : 'Check'}
              </button>
              <label className="button ghost file-button">
                <FileUp size={14} /> Upload
                <input
                  type="file"
                  accept="application/json,.json,text/html,.html,application/pdf,.pdf"
                  onChange={(event) => void uploadEvidence(source.id, event.target.files?.[0])}
                />
              </label>
            </article>
          ))}
        </div> : <EmptyState title="No approved sources" message="Add an official SCE rate page or tariff source, then run a check to create review candidates." action={<button className="button primary" onClick={() => { setShowSourceForm(true) }}><Plus size={15} /> Add source</button>} />}
      </Panel>

      <div className="candidate-layout">
        <Panel title="Candidate review queue" eyebrow="Source changes">
          {candidates.data?.length ? (
            <div className="candidate-list">
              {candidates.data.map((candidate) => (
                <button
                  key={candidate.id}
                  className={selected === candidate.id ? 'selected' : ''}
                  onClick={() => { setSelected(candidate.id); }}
                >
                  <span>
                    <strong>{candidate.summary.plan_code ?? 'Unmapped plan'}</strong>
                    <small>{formatTime(candidate.created_at)} · {candidate.summary.material_differences ?? 0} material changes</small>
                  </span>
                  <StatusPill status={candidate.status} />
                </button>
              ))}
            </div>
          ) : <EmptyState title="No candidates" message="A changed source will appear here after extraction and validation." />}
        </Panel>
        <Panel title="Difference review" eyebrow="Before and after">
          {!selected ? <EmptyState title="Choose a candidate" message="Select a source change to review exact normalized differences." />
            : detail.isLoading ? <LoadingState />
              : detail.error ? <ErrorState error={detail.error} />
                : detail.data && (
                  <>
                    <div className="candidate-review-head"><StatusPill status={detail.data.status} /><span>{detail.data.differences.length} normalized differences</span></div>
                    {detail.data.source_evidence && <div className="source-evidence"><Archive size={17} /><div><strong>Archived evidence</strong><small>{detail.data.source_evidence.parser_id} v{detail.data.source_evidence.parser_version} · SHA-256 {detail.data.source_evidence.sha256.slice(0, 16)}…</small><a href={`/api/v1/admin/rate-artifacts/${detail.data.source_evidence.artifact_id}/download`}>Download source artifact</a></div></div>}
                    <div className="diff-list">
                      {detail.data.differences.map((difference) => (
                        <article key={difference.path} className={difference.material ? 'material' : ''}>
                          <code>{difference.path}</code><span>{difference.change_type}</span>
                          <div><del>{JSON.stringify(difference.before)}</del><ins>{JSON.stringify(difference.after)}</ins></div>
                        </article>
                      ))}
                    </div>
                    {detail.data.status === 'pending_review' && (
                      <div className="candidate-actions">
                        <button className="button primary" onClick={() => { decide.mutate({ id: detail.data.id, action: 'approve', comment: 'Reviewed against archived source evidence.' }); }}><CheckCircle2 size={15} /> Approve</button>
                        <input aria-label="Rejection reason" placeholder="Reason required to reject" value={rejectReason} onChange={(event) => { setRejectReason(event.target.value); }} />
                        <button className="button danger" disabled={!rejectReason.trim()} onClick={() => { decide.mutate({ id: detail.data.id, action: 'reject', comment: rejectReason }); }}><XCircle size={15} /> Reject</button>
                      </div>
                    )}
                    {detail.data.status === 'approved' && <button className="button primary" onClick={() => { activate.mutate(detail.data.id); }}><CheckCircle2 size={15} /> Activate approved version</button>}
                  </>
                )}
        </Panel>
      </div>

      <Panel title="Recent source checks" eyebrow="Job history">
        <div className="simple-table"><table><thead><tr><th>Checked</th><th>Source</th><th>Outcome</th><th>HTTP</th><th>Error</th></tr></thead><tbody>
          {checks.data?.slice(0, 20).map((check) => <tr key={check.id}><td>{formatTime(check.checked_at)}</td><td><code>{check.rate_source_id.slice(0, 8)}</code></td><td><StatusPill status={check.outcome} /></td><td>{check.http_status ?? '—'}</td><td>{check.error_code ?? '—'}</td></tr>)}
        </tbody></table></div>
      </Panel>
    </>
  )
}
