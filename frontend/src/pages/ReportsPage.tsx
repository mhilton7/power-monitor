import { useMutation, useQuery } from '@tanstack/react-query'
import { Download, FileJson, FileSpreadsheet, Plus } from 'lucide-react'
import { api } from '../api'
import { EmptyState, formatTime, PageTitle, Panel, StatusPill } from '../components/UI'

interface Export { id: string; format: string; status: string; created_at: string; expires_at: string; content_hash?: string }

export function ReportsPage() {
  const exports = useQuery({ queryKey: ['exports'], queryFn: () => api<Export[]>('/api/v1/exports'), refetchInterval: 5000 })
  const create = useMutation({ mutationFn: (format: 'csv' | 'json') => api('/api/v1/exports', { method: 'POST', body: JSON.stringify({ format }) }), onSuccess: () => void exports.refetch() })
  return (
    <>
      <PageTitle eyebrow="Portable evidence" title="Reports & exports" description="Background jobs preserve data coverage, quality, rate version, calculation version, and authorization through expiration." actions={<button className="button primary" onClick={() => { create.mutate('csv'); }}><Plus size={17} /> New CSV export</button>} />
      <div className="report-types"><article><span><FileSpreadsheet /></span><p><strong>Raw & rollup CSV</strong><small>Spreadsheet-safe cells with UTC interval fields.</small></p><button className="button secondary" onClick={() => { create.mutate('csv'); }}>Create CSV</button></article><article><span><FileJson /></span><p><strong>Structured JSON</strong><small>Portable sequence, quality, and measurement records.</small></p><button className="button secondary" onClick={() => { create.mutate('json'); }}>Create JSON</button></article><article><span><Download /></span><p><strong>Billing summaries</strong><small>TOU buckets, cost components, rate source, and estimate disclosure.</small></p><button className="button secondary">Configure report</button></article></div>
      <Panel title="Export jobs" eyebrow="Files stream from protected storage">{exports.data?.length ? <div className="responsive-table"><table><thead><tr><th>Created</th><th>Format</th><th>Status</th><th>Expires</th><th>Integrity</th><th /></tr></thead><tbody>{exports.data.map((item) => <tr key={item.id}><td>{formatTime(item.created_at)}</td><td>{item.format.toUpperCase()}</td><td><StatusPill status={item.status} /></td><td>{formatTime(item.expires_at)}</td><td><code>{item.content_hash?.slice(0, 12) ?? 'Pending'}{item.content_hash ? '…' : ''}</code></td><td>{item.status === 'completed' && <a className="button ghost" href={`/api/v1/exports/${item.id}/download`}><Download size={15} /> Download</a>}</td></tr>)}</tbody></table></div> : <EmptyState title="No export jobs" message="Create a CSV or JSON export; large result sets run in the worker." />}</Panel>
    </>
  )
}

