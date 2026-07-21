import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowDown,
  ArrowDownToLine,
  ArrowUp,
  ArrowUpToLine,
  Download,
  Eye,
  GripVertical,
  History,
  Move,
  RotateCcw,
  Save,
  Search,
  Upload,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, apiDownload } from '../api'
import { StatusIndicatorZone } from '../components/StatusIndicators'
import { ErrorState, LoadingState, PageTitle, Panel, StatusPill, formatTime } from '../components/UI'
import type {
  StatusAdminCatalog,
  StatusBreakpoint,
  StatusIndicatorDefinition,
  StatusLayoutConfiguration,
  StatusLayoutDraftResponse,
  StatusLayoutItem,
  StatusLayoutRevisionSummary,
  StatusPreviewResponse,
} from '../types'

type ScopeBreakpoint = 'default' | StatusBreakpoint
type PreviewScenario = 'all_defaults' | 'one_disabled' | 'two_disabled' | 'one_only' | 'empty_zone' | 'many' | 'warning' | 'critical' | 'long_label'

const zoneLabel = (zone: string) => zone.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())

function scopeIdentity(item: StatusLayoutItem, page: string, role: string, breakpoint: ScopeBreakpoint): boolean {
  return item.page === page && item.role === role && item.breakpoint === breakpoint
}

function effectiveItem(
  configuration: StatusLayoutConfiguration,
  definition: StatusIndicatorDefinition,
  page: string,
  role: string,
  breakpoint: ScopeBreakpoint,
): StatusLayoutItem {
  const fallback: StatusLayoutItem = {
    indicator_key: definition.key,
    page: '*',
    role: '*',
    breakpoint: 'default',
    visible: definition.default_enabled,
    zone: definition.default_zone,
    order: definition.default_order,
    density: 'standard',
    show_icon: true,
    show_label: true,
    show_value: true,
    show_freshness: definition.freshness_supported,
    show_severity: true,
    show_tooltip: true,
  }
  const candidates = configuration.items
    .filter((item) => item.indicator_key === definition.key)
    .filter((item) => item.page === '*' || item.page === page)
    .filter((item) => item.role === '*' || item.role === role)
    .filter((item) => item.breakpoint === 'default' || item.breakpoint === breakpoint)
    .map((item) => ({
      item,
      score: (item.page === page ? 200 : 0) + (item.role === role ? 100 : 0) + (item.breakpoint === breakpoint ? 10 : 0),
    }))
    .sort((left, right) => left.score - right.score || left.item.role.localeCompare(right.item.role))
  return candidates.reduce<StatusLayoutItem>((result, candidate) => ({ ...result, ...candidate.item }), fallback)
}

function safeConfiguration(value?: StatusLayoutConfiguration): StatusLayoutConfiguration | undefined {
  return value ? structuredClone(value) : undefined
}

export function StatusIndicatorsPage({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient()
  const catalog = useQuery({
    queryKey: ['status-indicators', 'admin-catalog'],
    queryFn: () => api<StatusAdminCatalog>('/api/v1/admin/status-indicators/catalog'),
  })
  const draft = useQuery({
    queryKey: ['status-indicators', 'draft'],
    queryFn: () => api<StatusLayoutDraftResponse>('/api/v1/admin/status-indicators/draft'),
  })
  const revisions = useQuery({
    queryKey: ['status-indicators', 'revisions'],
    queryFn: () => api<{ revisions: StatusLayoutRevisionSummary[] }>('/api/v1/admin/status-indicators/revisions'),
  })
  const [configuration, setConfiguration] = useState<StatusLayoutConfiguration>()
  const [scopePage, setScopePage] = useState('*')
  const [scopeRole, setScopeRole] = useState('*')
  const [scopeBreakpoint, setScopeBreakpoint] = useState<ScopeBreakpoint>('default')
  const [previewPage, setPreviewPage] = useState('overview')
  const [previewRole, setPreviewRole] = useState('admin')
  const [previewBreakpoint, setPreviewBreakpoint] = useState<StatusBreakpoint>('desktop')
  const [previewScenario, setPreviewScenario] = useState<PreviewScenario>('all_defaults')
  const [preview, setPreview] = useState<StatusPreviewResponse>()
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('all')
  const [visibility, setVisibility] = useState('all')
  const [zoneFilter, setZoneFilter] = useState('all')
  const [announcement, setAnnouncement] = useState('')
  const [message, setMessage] = useState('')
  const [reason, setReason] = useState('')
  const [draggedKey, setDraggedKey] = useState<string>()
  const importRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!configuration && draft.data?.configuration) setConfiguration(safeConfiguration(draft.data.configuration))
  }, [configuration, draft.data?.configuration])

  const definitions = useMemo(() => catalog.data?.indicators ?? [], [catalog.data?.indicators])
  const currentItems = useMemo(() => definitions.map((definition) => ({
    definition,
    item: configuration ? effectiveItem(configuration, definition, scopePage, scopeRole, scopeBreakpoint) : undefined,
  })), [configuration, definitions, scopeBreakpoint, scopePage, scopeRole])
  const categories = useMemo(() => [...new Set(definitions.map((item) => item.category))].sort(), [definitions])
  const filtered = currentItems.filter(({ definition, item }) => {
    const term = search.trim().toLowerCase()
    if (term && !`${definition.default_label} ${definition.key} ${definition.description}`.toLowerCase().includes(term)) return false
    if (category !== 'all' && definition.category !== category) return false
    if (visibility === 'enabled' && item?.visible === false) return false
    if (visibility === 'disabled' && item?.visible !== false) return false
    if (zoneFilter !== 'all' && item?.zone !== zoneFilter) return false
    if (scopePage !== '*' && !definition.supported_pages.includes(scopePage) && !definition.global_shell_support) return false
    return true
  })
  const allEnabledByZone = useMemo(() => {
    const grouped = new Map<string, typeof currentItems>()
    for (const entry of currentItems.filter(({ item }) => item?.visible !== false)) {
      const zone = entry.item?.zone ?? entry.definition.default_zone
      grouped.set(zone, [...(grouped.get(zone) ?? []), entry])
    }
    for (const entries of grouped.values()) {
      entries.sort((left, right) => Number(left.item?.order ?? 0) - Number(right.item?.order ?? 0))
    }
    return grouped
  }, [currentItems])
  const enabledByZone = useMemo(() => {
    const grouped = new Map<string, typeof filtered>()
    for (const entry of filtered.filter(({ item }) => item?.visible !== false)) {
      const zone = entry.item?.zone ?? entry.definition.default_zone
      grouped.set(zone, [...(grouped.get(zone) ?? []), entry])
    }
    for (const entries of grouped.values()) {
      entries.sort((left, right) => Number(left.item?.order ?? 0) - Number(right.item?.order ?? 0))
    }
    return grouped
  }, [filtered])
  const disabled = filtered.filter(({ item }) => item?.visible === false)

  const updateItem = (definition: StatusIndicatorDefinition, patch: Partial<StatusLayoutItem>) => {
    if (!configuration) return
    const exactIndex = configuration.items.findIndex((item) => item.indicator_key === definition.key && scopeIdentity(item, scopePage, scopeRole, scopeBreakpoint))
    const base = effectiveItem(configuration, definition, scopePage, scopeRole, scopeBreakpoint)
    const next = structuredClone(configuration)
    const item = { ...base, ...patch, indicator_key: definition.key, page: scopePage, role: scopeRole, breakpoint: scopeBreakpoint }
    delete item.definition
    if (exactIndex >= 0) next.items[exactIndex] = item
    else next.items.push(item)
    setConfiguration(next)
  }

  const resetIndicator = (definition: StatusIndicatorDefinition) => {
    if (!configuration) return
    const next = structuredClone(configuration)
    next.items = next.items.filter((item) => !(item.indicator_key === definition.key && scopeIdentity(item, scopePage, scopeRole, scopeBreakpoint)))
    if (scopePage === '*' && scopeRole === '*' && scopeBreakpoint === 'default') {
      next.items.push({
        indicator_key: definition.key,
        page: '*', role: '*', breakpoint: 'default',
        visible: definition.default_enabled,
        zone: definition.default_zone,
        order: definition.default_order,
        density: 'standard',
        show_icon: true, show_label: true, show_value: true,
        show_freshness: definition.freshness_supported, show_severity: true, show_tooltip: true,
      })
    }
    setConfiguration(next)
    setAnnouncement(`${definition.default_label} restored to its default in this scope.`)
  }

  const applyPeerOrder = (peers: typeof currentItems) => {
    if (!configuration) return
    const next = structuredClone(configuration)
    peers.forEach((entry, order) => {
      const exactIndex = next.items.findIndex((candidate) => candidate.indicator_key === entry.definition.key && scopeIdentity(candidate, scopePage, scopeRole, scopeBreakpoint))
      const reordered = {
        ...effectiveItem(next, entry.definition, scopePage, scopeRole, scopeBreakpoint),
        indicator_key: entry.definition.key,
        page: scopePage,
        role: scopeRole,
        breakpoint: scopeBreakpoint,
        order: (order + 1) * 10,
      }
      delete reordered.definition
      if (exactIndex >= 0) next.items[exactIndex] = reordered
      else next.items.push(reordered)
    })
    setConfiguration(next)
  }

  const move = (definition: StatusIndicatorDefinition, direction: 'up' | 'down' | 'first' | 'last') => {
    const item = effectiveItem(configuration as StatusLayoutConfiguration, definition, scopePage, scopeRole, scopeBreakpoint)
    const peers = [...(allEnabledByZone.get(item.zone ?? definition.default_zone) ?? [])]
    const index = peers.findIndex((entry) => entry.definition.key === definition.key)
    if (index < 0) return
    const target = direction === 'first' ? 0 : direction === 'last' ? peers.length - 1 : Math.max(0, Math.min(peers.length - 1, index + (direction === 'up' ? -1 : 1)))
    const [moved] = peers.splice(index, 1)
    if (!moved || !configuration) return
    peers.splice(target, 0, moved)
    applyPeerOrder(peers)
    setAnnouncement(`${definition.default_label} moved ${direction}.`)
  }

  const save = useMutation({
    mutationFn: () => api<StatusLayoutDraftResponse>('/api/v1/admin/status-indicators/draft', {
      method: 'PUT',
      body: JSON.stringify({
        base_revision: draft.data?.base_revision ?? catalog.data?.published_revision ?? 0,
        draft_revision: draft.data?.draft_revision ?? 0,
        configuration,
        reason: reason || undefined,
      }),
    }),
    onSuccess: async (result) => {
      setConfiguration(safeConfiguration(result.configuration))
      setMessage(`Draft revision ${result.draft_revision} saved. Monitoring and alerts were not changed.`)
      await queryClient.invalidateQueries({ queryKey: ['status-indicators', 'draft'] })
    },
  })
  const previewMutation = useMutation({
    mutationFn: () => api<StatusPreviewResponse>('/api/v1/admin/status-indicators/preview', {
      method: 'POST',
      body: JSON.stringify({ configuration, page: previewPage, role: previewRole, breakpoint: previewBreakpoint, scenario: previewScenario }),
    }),
    onSuccess: async (result) => {
      setPreview(result)
      setMessage(`${previewBreakpoint} preview updated for ${previewRole}.`)
      await queryClient.invalidateQueries({ queryKey: ['status-indicators', 'draft'] })
    },
  })
  useEffect(() => {
    if (!configuration || !canManage) return
    const timer = window.setTimeout(() => { previewMutation.mutate() }, 350)
    return () => { window.clearTimeout(timer) }
    // The mutation object itself changes as requests settle; preview inputs are the intended trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [configuration, previewBreakpoint, previewPage, previewRole, previewScenario, canManage])

  const publish = useMutation({
    mutationFn: () => api('/api/v1/admin/status-indicators/publish', {
      method: 'POST',
      body: JSON.stringify({
        base_revision: draft.data?.base_revision,
        draft_revision: draft.data?.draft_revision,
        reason: reason || undefined,
        confirm: true,
        confirm_critical: Boolean(draft.data?.critical_hidden.length),
      }),
    }),
    onSuccess: async () => {
      setMessage('Published layout is active. Other signed-in dashboards refresh within 30 seconds.')
      setConfiguration(undefined)
      await queryClient.invalidateQueries({ queryKey: ['status-indicators'] })
    },
  })
  const confirmPublish = () => {
    const critical = draft.data?.critical_hidden ?? []
    const criticalCopy = critical.length
      ? `\n\nCritical summaries will be hidden in one or more scopes. Their monitoring remains active through these fallback workflows:\n${critical.map((item) => `• ${item.indicator_key}: ${item.fallback}`).join('\n')}`
      : ''
    if (window.confirm(`Publish this status layout for permitted users?${criticalCopy}`)) publish.mutate()
  }
  const restore = useMutation({
    mutationFn: (revision: StatusLayoutRevisionSummary) => api(`/api/v1/admin/status-indicators/revisions/${revision.id}/restore`, {
      method: 'POST',
      body: JSON.stringify({ base_revision: catalog.data?.published_revision, reason: `Restore revision ${revision.revision}`, confirm: true, confirm_critical: true }),
    }),
    onSuccess: async () => {
      setMessage('The selected revision was restored as a new immutable revision.')
      setConfiguration(undefined)
      await queryClient.invalidateQueries({ queryKey: ['status-indicators'] })
    },
  })
  const reset = useMutation({
    mutationFn: (scope: 'page' | 'all') => api<StatusLayoutDraftResponse>('/api/v1/admin/status-indicators/reset', {
      method: 'POST',
      body: JSON.stringify({ base_revision: draft.data?.base_revision, draft_revision: draft.data?.draft_revision, scope, page: scope === 'page' ? scopePage : undefined, reason: reason || undefined }),
    }),
    onSuccess: async (result) => {
      setConfiguration(safeConfiguration(result.configuration))
      setMessage('Defaults were restored into the draft. Preview and publish to activate them.')
      await queryClient.invalidateQueries({ queryKey: ['status-indicators', 'draft'] })
    },
  })

  const exportLayout = async () => {
    const blob = await apiDownload(`/api/v1/admin/status-indicators/export?draft=${draft.data?.exists ? 'true' : 'false'}`)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'power-monitor-status-layout.json'
    link.click()
    URL.revokeObjectURL(url)
  }
  const importLayout = async (file?: File) => {
    if (!file) return
    const document = JSON.parse(await file.text()) as { schema_version: string; registry_version: string; configuration: StatusLayoutConfiguration }
    const result = await api<StatusLayoutDraftResponse>('/api/v1/admin/status-indicators/import', {
      method: 'POST',
      body: JSON.stringify({ ...document, base_revision: draft.data?.base_revision ?? catalog.data?.published_revision ?? 0, reason: `Imported ${file.name}` }),
    })
    setConfiguration(safeConfiguration(result.configuration))
    setMessage('Layout imported into the draft. Preview is required before publishing.')
    await queryClient.invalidateQueries({ queryKey: ['status-indicators', 'draft'] })
  }

  if (catalog.isLoading || draft.isLoading) return <LoadingState label="Loading the registered status indicator catalog…" />
  if (catalog.error || draft.error) return <ErrorState error={catalog.error ?? draft.error} />
  if (!catalog.data || !draft.data || !configuration) return <ErrorState error={new Error('Status layout catalog was unavailable')} />
  const mutationError = save.error ?? previewMutation.error ?? publish.error ?? restore.error ?? reset.error

  return (
    <>
      <PageTitle
        eyebrow="Administration · presentation controls"
        title="Status Indicators & Layout"
        description="Choose which status indicators are visible, where they appear, and how the dashboard reorganizes them across screen sizes."
        actions={<div className="page-actions"><button className="button secondary" onClick={() => { void exportLayout() }}><Download size={16} /> Export</button>{canManage && <button className="button primary" onClick={() => { save.mutate() }} disabled={save.isPending}><Save size={16} /> Save draft</button>}</div>}
      />

      {message && <div className="success-banner" role="status">{message}</div>}
      {mutationError && <ErrorState error={mutationError} />}
      <p className="sr-only" aria-live="polite">{announcement}</p>

      <Panel eyebrow="Display only" title="Monitoring integrity">
        <div className="integrity-note"><StatusPill status="healthy" label="Monitoring remains active" /><p>Disabling an indicator removes only its rendered item and gap. Health checks, signed heartbeats, alerts, rate synchronization, backups, and audit records continue operating.</p></div>
      </Panel>

      <Panel eyebrow="Scope precedence" title="Edit layout">
        <div className="status-scope-grid">
          <label>Page<select value={scopePage} onChange={(event) => { setScopePage(event.target.value) }}><option value="*">Global default</option>{catalog.data.pages.map((page) => <option value={page} key={page}>{zoneLabel(page)}</option>)}</select></label>
          <label>Role<select value={scopeRole} onChange={(event) => { setScopeRole(event.target.value) }}><option value="*">All permitted roles</option>{catalog.data.roles.map((role) => <option value={role.id} key={role.id}>{role.label}</option>)}</select></label>
          <label>Breakpoint<select value={scopeBreakpoint} onChange={(event) => { setScopeBreakpoint(event.target.value as ScopeBreakpoint) }}><option value="default">Derived default</option>{catalog.data.breakpoints.map((item) => <option value={item} key={item}>{zoneLabel(item)}</option>)}</select></label>
          <label>Change reason<input value={reason} maxLength={500} onChange={(event) => { setReason(event.target.value) }} placeholder="Optional audit reason" /></label>
        </div>
        <p className="scope-help">Precedence: role + page → page → role → global published layout → compiled default. An exact breakpoint override wins within the same scope.</p>
        <div className="status-filter-grid">
          <label className="search-control"><Search size={16} /><span className="sr-only">Search indicators</span><input value={search} onChange={(event) => { setSearch(event.target.value) }} placeholder="Search label, key, or description" /></label>
          <label>Category<select value={category} onChange={(event) => { setCategory(event.target.value) }}><option value="all">All categories</option>{categories.map((item) => <option value={item} key={item}>{item}</option>)}</select></label>
          <label>Visibility<select value={visibility} onChange={(event) => { setVisibility(event.target.value) }}><option value="all">Enabled and disabled</option><option value="enabled">Enabled</option><option value="disabled">Disabled</option></select></label>
          <label>Zone<select value={zoneFilter} onChange={(event) => { setZoneFilter(event.target.value) }}><option value="all">All semantic zones</option>{catalog.data.zones.map((zone) => <option value={zone} key={zone}>{zoneLabel(zone)}</option>)}</select></label>
        </div>
      </Panel>

      {[...enabledByZone.entries()].map(([zone, entries]) => (
        <Panel key={zone} eyebrow="Semantic placement zone" title={zoneLabel(zone)} className="status-config-zone">
          <div className="status-config-list" onDragOver={(event) => { event.preventDefault() }}>
            {entries.map(({ definition, item }) => item && <article
              key={definition.key}
              draggable={canManage}
              onDragStart={() => { setDraggedKey(definition.key) }}
              onDragEnd={() => { setDraggedKey(undefined) }}
              onDrop={() => {
                const peers = [...(allEnabledByZone.get(zone) ?? [])]
                const dragged = peers.find((entry) => entry.definition.key === draggedKey)
                if (!dragged || dragged.definition.key === definition.key) return
                const draggedIndex = peers.findIndex((entry) => entry.definition.key === dragged.definition.key)
                const [moved] = peers.splice(draggedIndex, 1)
                const targetIndex = peers.findIndex((entry) => entry.definition.key === definition.key)
                if (!moved || targetIndex < 0) return
                peers.splice(targetIndex, 0, moved)
                applyPeerOrder(peers)
                setAnnouncement(`${dragged.definition.default_label} moved before ${definition.default_label}.`)
                setDraggedKey(undefined)
              }}
              data-indicator-key={definition.key}
            >
              <span className="drag-handle" title="Drag to reorder" aria-hidden="true"><GripVertical /></span>
              <div className="status-config-copy"><header><strong>{definition.default_label}</strong><code>{definition.key}</code>{catalog.data.new_indicator_keys.includes(definition.key) && <StatusPill status="pending" label="New" />}</header><p>{definition.description}</p><small>Data: {definition.data_source} · Permission: {definition.permission_required}</small>{definition.critical_fallback && <small className="critical-fallback">Critical fallback: {definition.critical_fallback}</small>}</div>
              <div className="status-config-controls">
                <label className="switch"><input type="checkbox" aria-label={`Show ${definition.default_label}`} checked={item.visible !== false} disabled={!canManage} onChange={(event) => {
                  if (!event.target.checked && definition.critical_fallback && !window.confirm(`${definition.default_label} has a critical fallback. Hide its display while keeping monitoring active?\n\n${definition.critical_fallback}`)) return
                  updateItem(definition, { visible: event.target.checked })
                }} /><span aria-hidden="true" /></label>
                <label>Zone<select value={item.zone} disabled={!canManage} onChange={(event) => { updateItem(definition, { zone: event.target.value }); setAnnouncement(`${definition.default_label} moved to ${zoneLabel(event.target.value)}.`) }}>{definition.allowed_zones.map((allowed) => <option value={allowed} key={allowed}>{zoneLabel(allowed)}</option>)}</select></label>
                <label>Density<select value={item.density} disabled={!canManage} onChange={(event) => { updateItem(definition, { density: event.target.value as StatusLayoutItem['density'] }) }}>{definition.presentations.map((density) => <option value={density} key={density}>{zoneLabel(density)}</option>)}</select></label>
                <details><summary>Content</summary><label><input type="checkbox" checked={item.show_icon !== false} disabled={!canManage || !definition.icon_supported} onChange={(event) => { updateItem(definition, { show_icon: event.target.checked }) }} /> Icon</label><label><input type="checkbox" checked={item.show_label !== false} disabled={!canManage || !definition.label_supported} onChange={(event) => { updateItem(definition, { show_label: event.target.checked }) }} /> Label</label><label><input type="checkbox" checked={item.show_value !== false} disabled={!canManage || !definition.value_supported} onChange={(event) => { updateItem(definition, { show_value: event.target.checked }) }} /> Value</label><label><input type="checkbox" checked={item.show_freshness !== false} disabled={!canManage || !definition.freshness_supported} onChange={(event) => { updateItem(definition, { show_freshness: event.target.checked }) }} /> Freshness</label><label><input type="checkbox" checked={item.show_severity !== false} disabled={!canManage} onChange={(event) => { updateItem(definition, { show_severity: event.target.checked }) }} /> Severity</label><label><input type="checkbox" checked={item.show_tooltip !== false} disabled={!canManage} onChange={(event) => { updateItem(definition, { show_tooltip: event.target.checked }) }} /> Tooltip</label></details>
                <div className="keyboard-order" aria-label={`Keyboard ordering for ${definition.default_label}`}><button className="icon-button" disabled={!canManage} onClick={() => { move(definition, 'first') }} aria-label="Move to beginning"><ArrowUpToLine /></button><button className="icon-button" disabled={!canManage} onClick={() => { move(definition, 'up') }} aria-label="Move up"><ArrowUp /></button><button className="icon-button" disabled={!canManage} onClick={() => { move(definition, 'down') }} aria-label="Move down"><ArrowDown /></button><button className="icon-button" disabled={!canManage} onClick={() => { move(definition, 'last') }} aria-label="Move to end"><ArrowDownToLine /></button></div>
                <button className="button ghost" disabled={!canManage} onClick={() => { resetIndicator(definition) }}><RotateCcw size={14} /> Reset</button>
              </div>
            </article>)}
          </div>
        </Panel>
      ))}

      <Panel eyebrow="Hidden from layout only" title="Disabled indicators" className="disabled-indicators">
        {disabled.length ? <div className="disabled-indicator-grid">{disabled.map(({ definition, item }) => <article key={definition.key}><div><strong>{definition.default_label}</strong><code>{definition.key}</code><p>{definition.description}</p></div><label>Placement<select value={item?.zone} disabled={!canManage} onChange={(event) => { updateItem(definition, { zone: event.target.value }) }}>{definition.allowed_zones.map((zone) => <option key={zone} value={zone}>{zoneLabel(zone)}</option>)}</select></label><button className="button secondary" disabled={!canManage} onClick={() => { updateItem(definition, { visible: true }) }}>Enable</button><button className="button ghost" disabled={!canManage} onClick={() => { resetIndicator(definition) }}><RotateCcw size={14} /> Default</button></article>)}</div> : <p className="empty-inline">No indicators are disabled in this scope.</p>}
      </Panel>

      <Panel eyebrow="Real values · safe sample fallbacks" title="Responsive preview" actions={<button className="button secondary" disabled={!canManage || previewMutation.isPending} onClick={() => { previewMutation.mutate() }}><Eye size={16} /> Refresh preview</button>}>
        <div className="preview-controls"><label>Page<select value={previewPage} onChange={(event) => { setPreviewPage(event.target.value) }}>{catalog.data.pages.map((page) => <option key={page} value={page}>{zoneLabel(page)}</option>)}</select></label><label>Role<select value={previewRole} onChange={(event) => { setPreviewRole(event.target.value) }}>{catalog.data.roles.map((role) => <option key={role.id} value={role.id}>{role.label}</option>)}</select></label><label>Viewport<select value={previewBreakpoint} onChange={(event) => { setPreviewBreakpoint(event.target.value as StatusBreakpoint) }}>{catalog.data.breakpoints.map((item) => <option key={item} value={item}>{zoneLabel(item)}</option>)}</select></label><label>Scenario<select value={previewScenario} onChange={(event) => { setPreviewScenario(event.target.value as PreviewScenario) }}><option value="all_defaults">Current draft</option><option value="one_disabled">One disabled</option><option value="two_disabled">Two disabled</option><option value="one_only">One only</option><option value="empty_zone">Empty zone</option><option value="many">Many indicators</option><option value="warning">Warning value</option><option value="critical">Critical value</option><option value="long_label">Long label</option></select></label></div>
        <div className={`status-layout-preview preview-${previewBreakpoint}`} data-testid="status-layout-preview">
          <header><span /><strong>Power Monitor preview</strong><small>{zoneLabel(previewPage)} · {zoneLabel(previewRole)}</small></header>
          <StatusIndicatorZone zone="global_header_left" layout={preview?.layout} values={preview?.values} />
          <StatusIndicatorZone zone="global_header_center" layout={preview?.layout} values={preview?.values} />
          <StatusIndicatorZone zone="global_header_right" layout={preview?.layout} values={preview?.values} />
          <StatusIndicatorZone zone="global_status_row" layout={preview?.layout} values={preview?.values} />
          <StatusIndicatorZone zone="mobile_header" layout={preview?.layout} values={preview?.values} />
          <StatusIndicatorZone zone="mobile_status_strip" layout={preview?.layout} values={preview?.values} />
          <main><h3>{zoneLabel(previewPage)}</h3><StatusIndicatorZone zone="page_header_primary" layout={preview?.layout} values={preview?.values} /><StatusIndicatorZone zone="page_header_secondary" layout={preview?.layout} values={preview?.values} /><StatusIndicatorZone zone="page_status_row" layout={preview?.layout} values={preview?.values} /><StatusIndicatorZone zone="page_summary_strip" layout={preview?.layout} values={preview?.values} /><div className="preview-content-placeholder"><span /><span /><span /></div><StatusIndicatorZone zone="page_footer" layout={preview?.layout} values={preview?.values} /></main>
          <StatusIndicatorZone zone="mobile_status_drawer" layout={preview?.layout} values={preview?.values} />
          <StatusIndicatorZone zone="global_footer" layout={preview?.layout} values={preview?.values} />
        </div>
      </Panel>

      <div className="status-admin-footer-grid">
        <Panel eyebrow="Immutable history" title="Published revisions">
          {revisions.data?.revisions.length ? <div className="revision-list">{revisions.data.revisions.map((revision) => <article key={revision.id}><div><strong>Revision {revision.revision}</strong><small>{formatTime(revision.created_at)}{revision.reason ? ` · ${revision.reason}` : ''}</small></div>{revision.revision === catalog.data.published_revision ? <StatusPill status="healthy" label="Current" /> : canManage && <button className="button ghost" onClick={() => { if (window.confirm(`Restore revision ${revision.revision} as a new revision?`)) restore.mutate(revision) }}><History size={14} /> Restore</button>}</article>)}</div> : <p className="empty-inline">The compiled default is active.</p>}
        </Panel>
        <Panel eyebrow="Safe JSON profile" title="Import, export & defaults">
          <div className="layout-utility-actions"><button className="button secondary" onClick={() => { void exportLayout() }}><Download size={16} /> Export {draft.data.exists ? 'draft' : 'published'}</button>{canManage && <><input ref={importRef} className="sr-only" type="file" accept="application/json,.json" onChange={(event) => { void importLayout(event.target.files?.[0]) }} /><button className="button secondary" onClick={() => { importRef.current?.click() }}><Upload size={16} /> Import to draft</button><button className="button secondary" disabled={scopePage === '*'} onClick={() => { reset.mutate('page') }}><RotateCcw size={16} /> Reset page</button><button className="button ghost danger-text" onClick={() => { if (window.confirm('Restore the complete compiled layout into the draft?')) reset.mutate('all') }}><RotateCcw size={16} /> Reset all</button></>}</div>
          {catalog.data.excluded_status_surfaces.map((item) => <p key={item.surface}><strong>{zoneLabel(item.surface)}:</strong> {item.reason}</p>)}
        </Panel>
      </div>

      {canManage && <aside className="sticky-action-bar"><div><span><Move size={16} /> Draft {draft.data.draft_revision || 'not saved'} · Published {catalog.data.published_revision}</span><small>{draft.data.previewed_revision === draft.data.draft_revision && draft.data.exists ? 'Current draft previewed' : 'Preview current draft before publishing'}</small></div><div><button className="button secondary" disabled={save.isPending} onClick={() => { save.mutate() }}><Save size={16} /> Save draft</button><button className="button primary" disabled={!draft.data.exists || draft.data.previewed_revision !== draft.data.draft_revision || publish.isPending} onClick={confirmPublish}>Publish layout</button></div></aside>}
    </>
  )
}
