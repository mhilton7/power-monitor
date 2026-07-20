import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, Eye, History, Monitor, RotateCcw, Save, Send, Smartphone, Upload, X, Zap } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api, ApiError } from '../api'
import { INTERFACE_TEXT_DEFAULTS } from '../interfaceText'
import type { InterfaceTextDefinition, InterfaceTextRevisionSummary } from '../types'
import { EmptyState, ErrorState, formatTime, LoadingState, PageTitle, Panel } from '../components/UI'

interface CatalogResponse { revision: number; definitions: InterfaceTextDefinition[] }
interface DraftResponse { exists: boolean; base_revision: number; draft_revision: number; previewed_revision?: number; values: Record<string, string>; reason?: string; updated_at?: string }
interface RevisionResponse extends InterfaceTextRevisionSummary { values: Record<string, string>; overrides: Record<string, string> }

const sections = ['General', 'Login Screen', 'Navigation', 'Page Titles & Subtitles', 'Footer & Support'] as const
type Section = (typeof sections)[number] | 'Preview' | 'Revision History'

function errorMessage(error: unknown): string | undefined {
  return error instanceof ApiError ? error.problem.detail : error instanceof Error ? error.message : undefined
}

export function InterfaceTextPage({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient()
  const [section, setSection] = useState<Section>('General')
  const [values, setValues] = useState<Record<string, string>>({})
  const [reason, setReason] = useState('')
  const [notice, setNotice] = useState<string>()
  const [showPublish, setShowPublish] = useState(false)
  const [restoreRevision, setRestoreRevision] = useState<InterfaceTextRevisionSummary>()
  const [restoreReason, setRestoreReason] = useState('')

  const catalog = useQuery({ queryKey: ['interface-text-catalog'], queryFn: () => api<CatalogResponse>('/api/v1/admin/interface-text/catalog') })
  const draft = useQuery({ queryKey: ['interface-text-draft'], queryFn: () => api<DraftResponse>('/api/v1/admin/interface-text/draft') })
  const revisions = useQuery({ queryKey: ['interface-text-revisions'], queryFn: () => api<{ revisions: InterfaceTextRevisionSummary[] }>('/api/v1/admin/interface-text/revisions') })

  useEffect(() => {
    if (!catalog.data || !draft.data) return
    const initial = Object.fromEntries(catalog.data.definitions.map((definition) => [definition.key, definition.current_value]))
    setValues({ ...initial, ...draft.data.values })
    setReason(draft.data.reason ?? '')
  }, [catalog.data, draft.data])

  const published = useMemo(() => Object.fromEntries((catalog.data?.definitions ?? []).map((definition) => [definition.key, definition.current_value])), [catalog.data])
  const changedValues = useMemo(() => Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== published[key])), [published, values])
  const dirtyCount = Object.keys(changedValues).length

  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['interface-text-catalog'] }),
      queryClient.invalidateQueries({ queryKey: ['interface-text-draft'] }),
      queryClient.invalidateQueries({ queryKey: ['interface-text-revisions'] }),
      queryClient.invalidateQueries({ queryKey: ['interface-text'] }),
      queryClient.invalidateQueries({ queryKey: ['public-interface-text'] }),
    ])
  }

  const saveDraftRequest = async () => api<DraftResponse>('/api/v1/admin/interface-text/draft', {
    method: 'PUT',
    body: JSON.stringify({ base_revision: catalog.data?.revision ?? 0, draft_revision: draft.data?.exists ? draft.data.draft_revision : undefined, values: changedValues, reason: reason || undefined }),
  })
  const saveDraft = useMutation({
    mutationFn: saveDraftRequest,
    onSuccess: async () => { setNotice('Text draft saved.'); await queryClient.invalidateQueries({ queryKey: ['interface-text-draft'] }) },
  })
  const preview = useMutation({
    mutationFn: async () => { await saveDraftRequest(); return api<{ draft_revision: number; values: Record<string, string> }>('/api/v1/admin/interface-text/preview', { method: 'POST' }) },
    onSuccess: async (result) => { setValues(result.values); setNotice('Preview generated from the validated draft. Nothing has been published.'); setSection('Preview'); await queryClient.invalidateQueries({ queryKey: ['interface-text-draft'] }) },
  })
  const publish = useMutation({
    mutationFn: () => api<RevisionResponse>('/api/v1/admin/interface-text/publish', { method: 'POST', body: JSON.stringify({ base_revision: draft.data?.base_revision ?? catalog.data?.revision ?? 0, draft_revision: draft.data?.draft_revision ?? 0, reason: reason || undefined, confirm: true }) }),
    onSuccess: async () => { setShowPublish(false); setNotice('Interface text published.'); await invalidate() },
  })
  const discard = useMutation({
    mutationFn: () => api<void>('/api/v1/admin/interface-text/draft', { method: 'DELETE' }),
    onSuccess: async () => { setNotice('Draft discarded.'); await queryClient.invalidateQueries({ queryKey: ['interface-text-draft'] }) },
  })
  const reset = useMutation({
    mutationFn: (target: { key?: string; section?: string }) => api<RevisionResponse>('/api/v1/admin/interface-text/reset', { method: 'POST', body: JSON.stringify({ base_revision: catalog.data?.revision ?? 0, ...target, reason: reason || `Restore ${target.key ?? target.section} defaults` }) }),
    onSuccess: async () => { setNotice('Defaults restored.'); await invalidate() },
  })
  const restore = useMutation({
    mutationFn: () => {
      if (!restoreRevision) throw new Error('Choose a revision to restore.')
      return api<RevisionResponse>(`/api/v1/admin/interface-text/revisions/${restoreRevision.id}/restore`, { method: 'POST', body: JSON.stringify({ base_revision: catalog.data?.revision ?? 0, reason: restoreReason || `Restore revision ${restoreRevision.revision}`, confirm: true }) })
    },
    onSuccess: async () => { setRestoreRevision(undefined); setRestoreReason(''); setNotice('Previous revision restored.'); await invalidate() },
  })
  const importDraft = useMutation({
    mutationFn: async (file: File) => {
      const parsed = JSON.parse(await file.text()) as { schema_version: string; values: Record<string, string> }
      return api<DraftResponse>('/api/v1/admin/interface-text/import', { method: 'POST', body: JSON.stringify({ ...parsed, base_revision: catalog.data?.revision ?? 0, reason: `Imported from ${file.name}` }) })
    },
    onSuccess: async () => { setNotice('Text imported into the draft. Preview before publishing.'); await queryClient.invalidateQueries({ queryKey: ['interface-text-draft'] }) },
  })

  const exportText = async (includeDraft: boolean) => {
    try {
      const payload = await api<Record<string, unknown>>(`/api/v1/admin/interface-text/export?draft=${includeDraft ? 'true' : 'false'}`)
      const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }))
      const link = document.createElement('a')
      link.href = url
      link.download = includeDraft ? 'power-monitor-interface-text-draft.json' : 'power-monitor-interface-text.json'
      link.click()
      URL.revokeObjectURL(url)
      setNotice(`${includeDraft ? 'Draft' : 'Published text'} exported.`)
    } catch (error) {
      setNotice(`Export failed: ${errorMessage(error) ?? 'unknown error'}`)
    }
  }

  const activeError = saveDraft.error ?? preview.error ?? publish.error ?? discard.error ?? reset.error ?? restore.error ?? importDraft.error
  const definitions = catalog.data?.definitions.filter((definition) => definition.section === section) ?? []

  return <>
    <PageTitle eyebrow="Administration · Approved interface catalog" title="Dashboard & Login Text" description="Customize approved interface labels and messages without changing application routes or security behavior." actions={<span className="revision-badge">Published revision {catalog.data?.revision ?? 0}</span>} />
    {notice && <div className="success-banner" role="status"><Save size={17} />{notice}<button className="icon-button" aria-label="Dismiss notification" onClick={() => { setNotice(undefined) }}><X size={16} /></button></div>}
    {activeError && <div className="form-error" role="alert"><strong>The text operation was not completed</strong><span>{errorMessage(activeError)}</span></div>}
    <div className="text-admin-toolbar">
      <div className="workspace-tabs text-tabs" role="tablist" aria-label="Interface text sections">{[...sections, 'Preview', 'Revision History'].map((item) => <button role="tab" key={item} aria-selected={section === item} onClick={() => { setSection(item as Section) }}>{item}</button>)}</div>
      <div className="text-primary-actions">
        {canManage && <button className="button secondary" disabled={saveDraft.isPending} onClick={() => { saveDraft.mutate() }}><Save size={16} />Save draft</button>}
        {canManage && <button className="button secondary" disabled={preview.isPending} onClick={() => { preview.mutate() }}><Eye size={16} />Preview</button>}
        {canManage && <button className="button primary" disabled={!draft.data?.exists || draft.data.previewed_revision !== draft.data.draft_revision || publish.isPending} title={draft.data?.exists && draft.data.previewed_revision !== draft.data.draft_revision ? 'Preview this draft revision before publishing' : undefined} onClick={() => { setShowPublish(true) }}><Send size={16} />Publish</button>}
      </div>
    </div>
    {catalog.isLoading || draft.isLoading ? <LoadingState label="Loading the approved text catalog…" /> : catalog.error || draft.error ? <ErrorState error={catalog.error ?? draft.error} /> : section === 'Preview' ? <InterfacePreview values={{ ...INTERFACE_TEXT_DEFAULTS, ...values }} /> : section === 'Revision History' ? (
      <Panel title="Published revisions" eyebrow="Immutable history" actions={<div className="inline-actions"><button className="button ghost" onClick={() => { void exportText(false) }}><Download size={15} />Export published</button>{canManage && <label className="button ghost file-button"><Upload size={15} />Import draft<input type="file" accept="application/json,.json" onChange={(event) => { const file = event.target.files?.[0]; if (file) importDraft.mutate(file); event.target.value = '' }} /></label>}</div>}>
        {revisions.isLoading ? <LoadingState /> : revisions.error ? <ErrorState error={revisions.error} /> : revisions.data?.revisions.length ? <div className="revision-list">{revisions.data.revisions.map((revision) => <article key={revision.id}><div><span className="revision-number">Revision {revision.revision}</span><strong>{revision.reason || 'No change reason supplied'}</strong><small>{formatTime(revision.created_at)} · {revision.changed_key_count} compiled override(s){revision.restored_from_id ? ' · restored revision' : ''}</small></div>{canManage && revision.revision !== catalog.data?.revision && <button className="button secondary" onClick={() => { setRestoreRevision(revision) }}><History size={15} />Restore</button>}</article>)}</div> : <EmptyState title="No published revisions" message="The first publication will appear here." />}
      </Panel>
    ) : <Panel title={section} eyebrow={`${definitions.length} approved fields`} actions={canManage ? <button className="button ghost" disabled={reset.isPending} onClick={() => { reset.mutate({ section }) }}><RotateCcw size={15} />Reset section to defaults</button> : undefined}>
      <div className="text-field-list">{definitions.map((definition) => <TextField key={definition.key} definition={definition} value={values[definition.key] ?? definition.current_value} changed={values[definition.key] !== definition.current_value} disabled={!canManage} onChange={(value) => { setValues((current) => ({ ...current, [definition.key]: value })) }} onReset={() => { reset.mutate({ key: definition.key }) }} />)}</div>
      {canManage && <label className="text-change-reason"><span>Draft change reason <small>optional, included in audit history</small></span><textarea maxLength={500} value={reason} onChange={(event) => { setReason(event.target.value) }} /></label>}
    </Panel>}
    {canManage && <footer className="sticky-action-bar"><span>{dirtyCount} unpublished local change(s){draft.data?.exists ? ` · saved draft revision ${draft.data.draft_revision}${draft.data.previewed_revision === draft.data.draft_revision ? ' · previewed' : ' · preview required'}` : ''}</span><div><button className="button ghost" disabled={!draft.data?.exists || discard.isPending} onClick={() => { discard.mutate() }}>Discard draft</button><button className="button ghost" onClick={() => { void exportText(true) }}><Download size={15} />Export draft</button><button className="button secondary" disabled={saveDraft.isPending} onClick={() => { saveDraft.mutate() }}><Save size={15} />Save draft</button><button className="button primary" disabled={!draft.data?.exists || draft.data.previewed_revision !== draft.data.draft_revision} title={draft.data?.exists && draft.data.previewed_revision !== draft.data.draft_revision ? 'Preview this draft revision before publishing' : undefined} onClick={() => { setShowPublish(true) }}><Send size={15} />Publish</button></div></footer>}
    {showPublish && <div className="modal-backdrop" role="presentation"><section className="confirm-dialog" role="dialog" aria-modal="true" aria-label="Publish interface text"><header><div><span className="eyebrow">Immutable revision</span><h2>Publish interface text?</h2></div><button className="icon-button" onClick={() => { setShowPublish(false) }} aria-label="Close publication confirmation"><X /></button></header><p>This makes the validated draft visible to dashboard users and the safe public login endpoint. Internal routes, API identifiers, and permission codes will not change.</p><dl className="detail-list"><div><dt>Base revision</dt><dd>{draft.data?.base_revision}</dd></div><div><dt>Draft revision</dt><dd>{draft.data?.draft_revision}</dd></div><div><dt>Changed fields</dt><dd>{Object.keys(draft.data?.values ?? {}).length}</dd></div></dl>{publish.error && <div className="form-error"><strong>Publication failed</strong><span>{errorMessage(publish.error)}</span></div>}<footer><button className="button secondary" onClick={() => { setShowPublish(false) }}>Cancel</button><button className="button primary" disabled={publish.isPending} onClick={() => { publish.mutate() }}>{publish.isPending ? 'Publishing…' : 'Confirm and publish'}</button></footer></section></div>}
    {restoreRevision && <div className="modal-backdrop" role="presentation"><section className="confirm-dialog" role="dialog" aria-modal="true" aria-label="Restore previous revision"><header><div><span className="eyebrow">Rollback creates new history</span><h2>Restore revision {restoreRevision.revision}?</h2></div><button className="icon-button" onClick={() => { setRestoreRevision(undefined) }} aria-label="Close restore confirmation"><X /></button></header><p>The historical revision stays immutable. A new published revision will copy its values.</p><label><span>Restore reason</span><textarea value={restoreReason} maxLength={500} onChange={(event) => { setRestoreReason(event.target.value) }} /></label>{restore.error && <div className="form-error"><strong>Restore failed</strong><span>{errorMessage(restore.error)}</span></div>}<footer><button className="button secondary" onClick={() => { setRestoreRevision(undefined) }}>Cancel</button><button className="button primary" disabled={restore.isPending} onClick={() => { restore.mutate() }}>Restore revision</button></footer></section></div>}
  </>
}

function TextField({ definition, value, changed, disabled, onChange, onReset }: { definition: InterfaceTextDefinition; value: string; changed: boolean; disabled: boolean; onChange: (value: string) => void; onReset: () => void }) {
  const id = `text-${definition.key.replaceAll('.', '-')}`
  return <article className={`text-field ${changed ? 'text-field-changed' : ''}`}><header><div><label htmlFor={id}>{definition.label}{definition.required && <span aria-label="required"> *</span>}</label><p>{definition.description}</p></div><div>{definition.visibility === 'public' && <span className="visibility-badge">Public login-safe</span>}{changed && <span className="changed-badge">Draft change</span>}</div></header>
    {definition.line_breaks || definition.max_length > 240 ? <textarea id={id} value={value} disabled={disabled} required={definition.required} minLength={definition.min_length} maxLength={definition.max_length} onChange={(event) => { onChange(event.target.value) }} /> : <input id={id} type={definition.field_type === 'url' ? 'url' : 'text'} value={value} disabled={disabled} required={definition.required} minLength={definition.min_length} maxLength={definition.max_length} onChange={(event) => { onChange(event.target.value) }} />}
    <footer><code>{definition.key}</code><span>{value.length}/{definition.max_length}</span>{!disabled && <button className="button ghost" type="button" onClick={onReset}><RotateCcw size={14} />Reset field to default</button>}</footer>
  </article>
}

function InterfacePreview({ values }: { values: Record<string, string> }) {
  const text = (key: string) => values[key] ?? INTERFACE_TEXT_DEFAULTS[key] ?? key
  return <div className="preview-grid">
    <Panel title="Desktop login" eyebrow="Sanitized draft preview"><div className="login-preview"><aside><div className="preview-brand"><Zap fill="currentColor" /> <span><strong>{text('general.application_name')}</strong><small>{text('general.organization_tagline')}</small></span></div><h3>Your energy.<br />Clearly measured.</h3></aside><section><span className="eyebrow">Welcome back</span><h2>{text('login.heading')}</h2><p>{text('login.subtitle')}</p><label><span>{text('login.email_label')}</span><input disabled /></label><label><span>{text('login.password_label')}</span><input disabled type="password" /></label><p>{text('login.help_text')}</p>{text('login.support_url') && <a href={text('login.support_url')} rel="noreferrer">{text('login.support_label')}</a>}<button className="button primary" disabled>{text('login.sign_in_button')}</button><small>{text('login.footer')}</small></section></div></Panel>
    <Panel title="Mobile login" eyebrow="320-pixel representative"><div className="mobile-preview"><Smartphone aria-hidden="true" /><div><strong>{text('general.application_short_name')}</strong><h3>{text('login.heading')}</h3><p>{text('login.subtitle')}</p><button className="button primary" disabled>{text('login.sign_in_button')}</button></div></div></Panel>
    <Panel title="Dashboard shell" eyebrow="Navigation, header, banner, and footer" className="dashboard-preview-panel"><div className="dashboard-preview"><aside><strong><Zap size={15} fill="currentColor" />{text('general.application_short_name')}</strong>{['overview', 'devices', 'topology', 'history', 'rates', 'alerts', 'enrollment', 'administration', 'users_access', 'interface_text'].map((key) => <span key={key}>{text(`navigation.${key}`)}</span>)}</aside><section>{text('footer.banner') && <div className="dashboard-banner">{text('footer.banner')}</div>}<header><Monitor size={18} /><small>Representative authenticated page</small></header><h2>{text('pages.overview.title')}</h2><p>{text('pages.overview.subtitle')}</p><div className="preview-card-row"><span /><span /><span /></div><footer>{text('footer.dashboard')} {text('footer.support_label')} {text('footer.copyright')}</footer></section></div></Panel>
  </div>
}
