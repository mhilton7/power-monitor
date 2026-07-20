import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArchiveRestore, CheckCircle2, RadioTower, Trash2, X } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api, ApiError } from '../api'
import type { Device } from '../types'
import { EmptyState, ErrorState, formatTime, LoadingState, Panel, StatusPill } from './UI'

interface DeviceDetail {
  device: Device & { hardware_id: string }
  history: {
    reading_count: number
    earliest_reading_at?: string
    latest_reading_at?: string
    retained: boolean
  }
}

interface RemoveSensorDialogProps {
  device: Device
  onClose: () => void
  onRemoved: () => void
}

function RemoveSensorDialog({ device, onClose, onRemoved }: RemoveSensorDialogProps) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [confirmation, setConfirmation] = useState('')
  const [reason, setReason] = useState('')
  const detail = useQuery({
    queryKey: ['device-removal-detail', device.id],
    queryFn: () => api<DeviceDetail>(`/api/v1/devices/${device.id}`),
  })
  const remove = useMutation({
    mutationFn: () => api(`/api/v1/admin/devices/${device.id}/unclaim`, {
      method: 'POST',
      body: JSON.stringify({ confirmation, reason: reason || null }),
    }),
    onSuccess: onRemoved,
  })
  useEffect(() => {
    const current = dialog.current
    current?.showModal()
    return () => { if (current?.open) current.close() }
  }, [])
  const confirmed = confirmation === device.name || confirmation === device.id
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (confirmed) remove.mutate()
  }
  const problem = remove.error instanceof ApiError ? remove.error.problem : undefined
  return (
    <dialog ref={dialog} className="sensor-removal-dialog" onCancel={(event) => { event.preventDefault(); if (!remove.isPending) onClose() }} aria-labelledby="remove-sensor-title">
      <form onSubmit={submit}>
        <header><div><span className="eyebrow">Credential revocation</span><h2 id="remove-sensor-title">Remove sensor</h2></div><button type="button" className="icon-button" aria-label="Close remove sensor dialog" disabled={remove.isPending} onClick={onClose}><X /></button></header>
        {detail.isLoading ? <LoadingState label="Loading retained-history details…" /> : detail.error ? <ErrorState error={detail.error} retry={() => { void detail.refetch() }} /> : (
          <>
            <dl className="sensor-removal-summary">
              <div><dt>Friendly name</dt><dd>{device.name}</dd></div>
              <div><dt>Immutable device ID</dt><dd><code>{device.id}</code></dd></div>
              <div><dt>Assignment</dt><dd>{device.site_name ?? device.site_id} · {device.circuit_name ?? 'No circuit'}</dd></div>
              <div><dt>Last seen</dt><dd>{formatTime(device.last_seen_at)}</dd></div>
              <div><dt>Retained readings</dt><dd>{detail.data?.history.reading_count.toLocaleString() ?? 0}</dd></div>
              <div><dt>Historical range</dt><dd>{detail.data?.history.earliest_reading_at ? `${formatTime(detail.data.history.earliest_reading_at)} to ${formatTime(detail.data.history.latest_reading_at)}` : 'No historical readings'}</dd></div>
            </dl>
            <div className="removal-impact"><strong>This safely decommissions the sensor.</strong><ul><li>All device credentials are revoked.</li><li>Polling, signed heartbeats, and synchronization stop.</li><li>The sensor disappears from active and claimed lists.</li><li>Readings, cost calculations, alerts, and audit records remain available.</li><li>Re-enrollment requires a new one-time token and newly generated credentials.</li></ul></div>
            <label><span>Removal reason <small>optional</small></span><select value={reason} onChange={(event) => { setReason(event.target.value) }}><option value="">No reason supplied</option><option value="replaced">Replaced</option><option value="moved">Moved</option><option value="failed_hardware">Failed hardware</option><option value="duplicate_enrollment">Duplicate enrollment</option><option value="testing_device">Testing device</option><option value="other">Other</option></select></label>
            <label><span>Type <strong>{device.name}</strong> or the immutable ID to confirm</span><input autoFocus value={confirmation} onChange={(event) => { setConfirmation(event.target.value) }} aria-describedby="sensor-confirmation-help" /></label>
            <p id="sensor-confirmation-help" className="field-help">The value must match exactly. This action does not factory-reset the physical ESP32.</p>
            {confirmation && !confirmed && <p className="field-error" role="alert">The confirmation does not match the sensor name or ID.</p>}
            {problem && <div className="form-error" role="alert"><strong>{problem.title}</strong><span>{problem.detail}</span></div>}
            <footer><button type="button" className="button secondary" disabled={remove.isPending} onClick={onClose}>Cancel</button><button className="button danger" disabled={!confirmed || remove.isPending}><Trash2 size={16} />{remove.isPending ? 'Removing sensor…' : 'Remove sensor'}</button></footer>
          </>
        )}
      </form>
    </dialog>
  )
}

export function ClaimedSensors() {
  const client = useQueryClient()
  const [view, setView] = useState<'active' | 'decommissioned'>('active')
  const [selected, setSelected] = useState<Device>()
  const [success, setSuccess] = useState<string>()
  const devices = useQuery({
    queryKey: ['claimed-sensors', view],
    queryFn: () => api<Device[]>(`/api/v1/devices?lifecycle=${view}`),
  })
  const removed = async () => {
    setSelected(undefined)
    setSuccess('Sensor removed successfully.')
    await Promise.all([
      client.invalidateQueries({ queryKey: ['claimed-sensors'] }),
      client.invalidateQueries({ queryKey: ['devices'] }),
      client.invalidateQueries({ queryKey: ['fleet'] }),
    ])
  }
  return (
    <Panel title="Claimed sensors" eyebrow="Administrator lifecycle control" actions={<div className="segmented-control" role="tablist" aria-label="Sensor lifecycle"><button type="button" role="tab" aria-selected={view === 'active'} onClick={() => { setView('active'); setSuccess(undefined) }}>Active</button><button type="button" role="tab" aria-selected={view === 'decommissioned'} onClick={() => { setView('decommissioned'); setSuccess(undefined) }}><ArchiveRestore size={15} /> Archived sensors</button></div>}>
      {success && <div className="form-success" role="status"><CheckCircle2 /> {success}</div>}
      {devices.isLoading ? <LoadingState /> : devices.error ? <ErrorState error={devices.error} retry={() => { void devices.refetch() }} /> : devices.data?.length ? (
        <div className="responsive-table"><table><thead><tr><th>Sensor</th><th>{view === 'active' ? 'Assignment' : 'Removed'}</th><th>Status</th><th>{view === 'active' ? 'Last seen' : 'Retained history'}</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{devices.data.map((item) => <tr key={item.id}><td><div className="device-cell"><span><RadioTower /></span><p><strong>{item.name}</strong><small><code>{item.id}</code></small></p></div></td><td>{view === 'active' ? <>{item.site_name ?? item.site_id}<small className="table-subtext">{item.circuit_name ?? 'No circuit assigned'}</small></> : <>{formatTime(item.decommissioned_at)}<small className="table-subtext">{item.decommissioned_by_name ?? 'Administrator'} · {item.decommission_reason?.replaceAll('_', ' ') ?? 'No reason'}</small></>}</td><td><StatusPill status={view === 'active' ? item.status : 'decommissioned'} label={view === 'active' ? undefined : 'Removed'} /></td><td>{view === 'active' ? formatTime(item.last_seen_at) : <><strong>{item.retained_history ? 'Preserved' : 'Unknown'}</strong><small className="table-subtext">Re-enrollment {item.re_enrollment_allowed ? 'allowed' : 'unavailable'}</small></>}</td><td className="table-actions">{view === 'active' && <button className="button ghost danger-text" onClick={() => { setSelected(item); setSuccess(undefined) }}><Trash2 size={15} /> Remove sensor</button>}</td></tr>)}</tbody></table></div>
      ) : <EmptyState title={view === 'active' ? 'No claimed sensors' : 'No archived sensors'} message={view === 'active' ? 'Claim an enrollment token to add a sensor.' : 'Safely removed sensors will remain visible here with their retained-history status.'} />}
      {selected && <RemoveSensorDialog device={selected} onClose={() => { setSelected(undefined) }} onRemoved={() => { void removed() }} />}
    </Panel>
  )
}
