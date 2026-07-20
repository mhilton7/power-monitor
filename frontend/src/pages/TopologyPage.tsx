import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Braces, CircuitBoard, GitBranch, Plus, Split } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { ApiError, api } from '../api'
import type { Device, Site } from '../types'
import { EmptyState, ErrorState, LoadingState, PageTitle, Panel, StatusPill } from '../components/UI'

interface Circuit {
  id: string
  site_id: string
  parent_id?: string
  name: string
  measurement_role: string
  split_phase_group?: string
}

interface Aggregate {
  id: string
  name: string
  cost_scope: string
  is_default: boolean
  members: Array<{ circuit_id?: string; device_id?: string }>
  overlap_confirmed_at?: string
}

function CircuitNode({ circuit, all, depth = 0 }: { circuit: Circuit; all: Circuit[]; depth?: number }) {
  const children = all.filter((item) => item.parent_id === circuit.id)
  return (
    <li style={{ '--depth': depth } as React.CSSProperties}>
      <div className="circuit-node"><span className="circuit-glyph"><CircuitBoard /></span><p><strong>{circuit.name}</strong><small>{circuit.measurement_role}{circuit.split_phase_group ? ` · ${circuit.split_phase_group}` : ''}</small></p><StatusPill status="healthy" label="configured" /></div>
      {children.length > 0 && <ul>{children.map((child) => <CircuitNode key={child.id} circuit={child} all={all} depth={depth + 1} />)}</ul>}
    </li>
  )
}

export function TopologyPage() {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState('')
  const [role, setRole] = useState('branch')
  const [parentId, setParentId] = useState('')
  const sites = useQuery({ queryKey: ['sites'], queryFn: () => api<Site[]>('/api/v1/sites') })
  const circuits = useQuery({ queryKey: ['circuits'], queryFn: () => api<Circuit[]>('/api/v1/circuits') })
  const aggregates = useQuery({ queryKey: ['aggregates'], queryFn: () => api<Aggregate[]>('/api/v1/aggregate-sets') })
  const devices = useQuery({ queryKey: ['devices'], queryFn: () => api<Device[]>('/api/v1/devices') })
  const create = useMutation({
    mutationFn: () => api<Circuit>('/api/v1/circuits', { method: 'POST', body: JSON.stringify({ site_id: sites.data?.[0]?.id, parent_id: parentId || null, name, measurement_role: role, split_phase_group: null }) }),
    onSuccess: async () => { setName(''); setEditing(false); await queryClient.invalidateQueries({ queryKey: ['circuits'] }) },
  })
  const submit = (event: FormEvent) => { event.preventDefault(); create.mutate() }
  if (sites.isLoading || circuits.isLoading) return <LoadingState />
  if (sites.error || circuits.error) return <ErrorState error={sites.error ?? circuits.error} />
  const roots = (circuits.data ?? []).filter((circuit) => !circuit.parent_id)
  return (
    <>
      <PageTitle eyebrow="Electrical model" title="Site & circuit topology" description="Make overlap explicit so parent, service-leg, branch, and submeter readings never become an accidental total." actions={<button className="button primary" onClick={() => { setEditing(!editing); }}><Plus size={17} /> Add circuit</button>} />
      {editing && <Panel title="New circuit" eyebrow="Validated hierarchy"><form className="inline-form" onSubmit={submit}><label><span>Name</span><input value={name} onChange={(event) => { setName(event.target.value); }} required /></label><label><span>Measurement role</span><select value={role} onChange={(event) => { setRole(event.target.value); }}><option value="main">Main</option><option value="service-leg">Service leg</option><option value="branch">Branch</option><option value="submeter">Submeter</option><option value="informational">Informational</option></select></label><label><span>Parent circuit</span><select value={parentId} onChange={(event) => { setParentId(event.target.value); }}><option value="">None — root</option>{circuits.data?.map((circuit) => <option key={circuit.id} value={circuit.id}>{circuit.name}</option>)}</select></label><button className="button primary" disabled={create.isPending}>Save circuit</button></form>{create.error instanceof ApiError && <p className="field-error">{create.error.problem.detail}</p>}</Panel>}
      <div className="topology-grid">
        <Panel title={sites.data?.[0]?.name ?? 'Site'} eyebrow="Circuit tree" actions={<span className="count-badge">{circuits.data?.length ?? 0} circuits</span>}>
          {roots.length ? <ul className="circuit-tree">{roots.map((root) => <CircuitNode key={root.id} circuit={root} all={circuits.data ?? []} />)}</ul> : <EmptyState title="No circuit tree yet" message="Add a main, service leg, or branch, then assign sensors." />}
        </Panel>
        <Panel title="Aggregate sets" eyebrow="Explicit inclusion policy">
          {(aggregates.data?.length ?? 0) ? <div className="aggregate-list">{aggregates.data?.map((aggregate) => <article key={aggregate.id}><header><span className="aggregate-icon"><Braces /></span><div><strong>{aggregate.name}</strong><small>{aggregate.members.length} explicit members</small></div><StatusPill status={aggregate.overlap_confirmed_at ? 'pending' : 'healthy'} label={aggregate.cost_scope} /></header>{aggregate.overlap_confirmed_at && <p className="warning-text"><AlertTriangle size={15} /> Potential overlap was explicitly confirmed.</p>}</article>)}</div> : <EmptyState title="No aggregate sets" message="A total includes nothing until an administrator explicitly selects non-overlapping members." />}
          <div className="topology-rule"><GitBranch /><p><strong>Parent + child</strong><small>Never summed by default</small></p></div><div className="topology-rule"><Split /><p><strong>Split-phase legs</strong><small>Only paired service legs form a service total</small></p></div>
        </Panel>
      </div>
      <Panel title="Device assignments" eyebrow="Measurement boundary"><div className="responsive-table"><table><thead><tr><th>Sensor</th><th>Role</th><th>Circuit</th><th>Default aggregate</th><th>Cost scope</th></tr></thead><tbody>{devices.data?.map((device) => <tr key={device.id}><td><strong>{device.name}</strong></td><td>{device.measurement_role}</td><td>{circuits.data?.find((item) => item.id === device.circuit_id)?.name ?? 'Unassigned'}</td><td>{device.included_in_default ? 'Included' : 'Excluded'}</td><td><StatusPill status="healthy" label={device.cost_scope} /></td></tr>)}</tbody></table></div></Panel>
    </>
  )
}

