import { useMutation, useQuery } from '@tanstack/react-query'
import { CheckCircle2, ClipboardCheck, Copy, ExternalLink, FileJson, Plus } from 'lucide-react'
import { api } from '../api'
import { EmptyState, ErrorState, LoadingState, PageTitle, Panel, StatusPill } from '../components/UI'

interface RateVersion { id: string; version: number; effective_from: string; effective_to?: string; source_url: string; source_checked_on: string; source_notes: string; content_hash: string; immutable_after_use: boolean; is_active: boolean }
interface RatePlan { id: string; code: string; name: string; description: string; versions: RateVersion[] }

export function RatesPage() {
  const query = useQuery({ queryKey: ['rates'], queryFn: () => api<RatePlan[]>('/api/v1/rate-plans') })
  const activate = useMutation({ mutationFn: (id: string) => api<{ active: boolean }>(`/api/v1/rate-versions/${id}/activate`, { method: 'POST' }), onSuccess: () => void query.refetch() })
  return (
    <>
      <PageTitle eyebrow="Tariff library" title="Rate plans" description="Effective-dated, source-labeled versions keep past reports reproducible while future prices remain editable." actions={<button className="button primary"><Plus size={17} /> Custom plan</button>} />
      {query.isLoading ? <LoadingState /> : query.error ? <ErrorState error={query.error} retry={() => void query.refetch()} /> : query.data?.length ? <div className="rate-grid">{query.data.map((plan) => { const version = plan.versions[0]; return <Panel key={plan.id} className="rate-card"><header className="rate-card-head"><div><span className="plan-code">{plan.code}</span><h2>{plan.name}</h2></div>{version?.is_active ? <StatusPill status="healthy" label="Active" /> : <StatusPill status="pending" label="Available" />}</header><p>{plan.description}</p>{version && <><dl className="rate-meta"><div><dt>Effective</dt><dd>{version.effective_from}</dd></div><div><dt>Source checked</dt><dd>{version.source_checked_on}</dd></div><div><dt>Version</dt><dd>v{version.version}</dd></div><div><dt>Integrity</dt><dd title={version.content_hash}>{version.content_hash.slice(0, 10)}…</dd></div></dl><div className="source-note"><ClipboardCheck size={17} /><p>{version.source_notes}</p></div><footer><a href={version.source_url} target="_blank" rel="noreferrer">SCE source <ExternalLink size={14} /></a><div><button className="button ghost"><FileJson size={15} /> Export</button><button className="button secondary"><Copy size={15} /> Clone</button>{!version.is_active && <button className="button primary" onClick={() => { activate.mutate(version.id); }}><CheckCircle2 size={15} /> Activate</button>}</div></footer></>}</Panel>})}</div> : <EmptyState title="No rate plans" message="Run first-time initialization to install effective-dated SCE presets." />}
      <Panel title="Activation checklist" eyebrow="Before calculating costs"><div className="checklist"><div><span>1</span><p><strong>Verify the plan code</strong><small>Find it on the current SCE bill.</small></p></div><div><span>2</span><p><strong>Check source date and prices</strong><small>Public rates can change after this bundled seed.</small></p></div><div><span>3</span><p><strong>Set the cost scope</strong><small>One-CT devices default to energy-only.</small></p></div><div><span>4</span><p><strong>Configure provider adjustments</strong><small>CCA and Direct Access generation are separate.</small></p></div></div></Panel>
    </>
  )
}
