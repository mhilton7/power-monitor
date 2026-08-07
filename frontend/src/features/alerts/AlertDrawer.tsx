import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Bell, Check, ChevronDown, ChevronRight, Clock3, ExternalLink, EyeOff, Trash2, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from '../../app/router'
import { hasPermission } from '../../access/permissions'
import { request } from '../../api/client'
import { EmptyState } from '../../components/feedback/States'
import { useAuth } from '../../state/AuthContext'
import type { AlertSummary } from '../../types/models'
import { dateTime, relativeTime, statusLabel } from '../../utils/format'
import { groupNotifications, removeCachedNotification, updateCachedNotification, type NotificationPageCache } from './notificationSelectors'

export function AlertDrawer({ open, alerts, onClose }: { open: boolean; alerts: AlertSummary[]; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const { session } = useAuth()
  const canAcknowledge = hasPermission(session, 'alerts.acknowledge')
  const canManageDelivery = hasPermission(session, 'alerts.manage_delivery')
  const [expanded, setExpanded] = useState<string>()
  const [silencing, setSilencing] = useState<AlertSummary>()
  const [suppressing, setSuppressing] = useState<AlertSummary>()
  const [removing, setRemoving] = useState<AlertSummary>()
  const [clearingResolved, setClearingResolved] = useState<AlertSummary[]>()

  useEffect(() => {
    if (!open) return
    closeRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKeyDown)
    return () => { document.removeEventListener('keydown', onKeyDown) }
  }, [onClose, open])

  const groups = useMemo(() => groupNotifications(alerts), [alerts])

  if (!open) return null
  return (
    <>
      <button type="button" className="drawer-backdrop" aria-label="Close notifications" onClick={onClose} />
      <aside className="alert-drawer notification-center" role="dialog" aria-modal="true" aria-labelledby="alert-drawer-title">
        <header>
          <div><span className="icon-tile"><Bell aria-hidden="true" /></span><div><h2 id="alert-drawer-title">Notifications</h2><p>{groups.active.length} active {groups.active.length === 1 ? 'issue' : 'issues'} · {groups.recommendations.length} {groups.recommendations.length === 1 ? 'recommendation' : 'recommendations'}</p></div></div>
          <button ref={closeRef} type="button" className="icon-button" aria-label="Close notifications" onClick={onClose}><X /></button>
        </header>
        <div className="drawer-content">
          {alerts.length === 0 ? <EmptyState title="Everything looks calm" message="There are no active issues or recommendations for your home." /> : <>
            <NotificationSection title="Active issues" items={groups.active} expanded={expanded} setExpanded={setExpanded} canAcknowledge={canAcknowledge} canManageDelivery={canManageDelivery} onSilence={setSilencing} onSuppress={setSuppressing} onRemove={setRemoving} onClose={onClose} />
            <NotificationSection title="Recommendations" items={groups.recommendations} expanded={expanded} setExpanded={setExpanded} canAcknowledge={canAcknowledge} canManageDelivery={canManageDelivery} onSilence={setSilencing} onSuppress={setSuppressing} onRemove={setRemoving} onClose={onClose} />
            <NotificationSection title="Recently resolved" items={groups.resolved} total={groups.resolvedAll.length} expanded={expanded} setExpanded={setExpanded} canAcknowledge={false} canManageDelivery={false} onSilence={setSilencing} onSuppress={setSuppressing} onRemove={setRemoving} onClearAll={() => { setClearingResolved(groups.resolvedAll) }} onClose={onClose} />
          </>}
        </div>
      </aside>
      {silencing && <SilenceDialog notification={silencing} onClose={() => { setSilencing(undefined) }} />}
      {suppressing && <SuppressDialog notification={suppressing} onClose={() => { setSuppressing(undefined) }} />}
      {removing && <RemoveNotificationDialog notification={removing} onClose={() => { setRemoving(undefined) }} />}
      {clearingResolved && <ClearResolvedNotificationsDialog notifications={clearingResolved} onClose={() => { setClearingResolved(undefined) }} />}
    </>
  )
}

function NotificationSection({ title, items, total, expanded, setExpanded, canAcknowledge, canManageDelivery, onSilence, onSuppress, onRemove, onClearAll, onClose }: {
  title: string
  items: AlertSummary[]
  total?: number
  expanded?: string
  setExpanded: (id?: string) => void
  canAcknowledge: boolean
  canManageDelivery: boolean
  onSilence: (notification: AlertSummary) => void
  onSuppress: (notification: AlertSummary) => void
  onRemove: (notification: AlertSummary) => void
  onClearAll?: () => void
  onClose: () => void
}) {
  if (!items.length) return null
  const headingId = `notification-${title.replaceAll(' ', '-').toLowerCase()}`
  return <section className="notification-section" aria-labelledby={headingId}><div className="notification-section-heading"><h3 id={headingId}>{title}<span>{total ?? items.length}</span></h3>{onClearAll && <button type="button" className="button ghost compact" onClick={onClearAll}>Clear all</button>}</div><ul className="alert-list">{items.map((item) => <NotificationRow key={item.id} item={item} open={expanded === item.id} onToggle={() => { setExpanded(expanded === item.id ? undefined : item.id) }} canAcknowledge={canAcknowledge} canManageDelivery={canManageDelivery} onSilence={() => { onSilence(item) }} onSuppress={() => { onSuppress(item) }} onRemove={() => { onRemove(item) }} onClose={onClose} />)}</ul></section>
}

function NotificationRow({ item, open, onToggle, canAcknowledge, canManageDelivery, onSilence, onSuppress, onRemove, onClose }: {
  item: AlertSummary
  open: boolean
  onToggle: () => void
  canAcknowledge: boolean
  canManageDelivery: boolean
  onSilence: () => void
  onSuppress: () => void
  onRemove: () => void
  onClose: () => void
}) {
  const client = useQueryClient()
  const navigate = useNavigate()
  const acknowledge = useMutation({
    mutationFn: () => request(`/api/v1/notifications/${encodeURIComponent(item.id)}/acknowledge`, { method: 'POST', body: JSON.stringify({ note: 'Acknowledged from notification center' }) }),
    onMutate: async () => {
      await client.cancelQueries({ queryKey: ['alerts'] })
      const previous = client.getQueriesData<NotificationPageCache>({ queryKey: ['alerts'] })
      client.setQueriesData<NotificationPageCache>({ queryKey: ['alerts'] }, (current) => (
        updateCachedNotification(current, item.id, (notification) => ({
          ...notification,
          status: 'acknowledged',
        }))
      ))
      return { previous }
    },
    onError: (_error, _variables, context) => {
      context?.previous.forEach(([queryKey, value]) => {
        client.setQueryData(queryKey, value)
      })
    },
    onSettled: async () => client.invalidateQueries({ queryKey: ['alerts'] }),
  })
  const endSilence = useMutation({ mutationFn: () => request(`/api/v1/notifications/${encodeURIComponent(item.id)}/silence`, { method: 'DELETE' }), onSuccess: async () => client.invalidateQueries({ queryKey: ['alerts'] }) })
  const observed = item.observed ? `${item.observed.value}${item.observed.unit ? ` ${item.observed.unit}` : ''}` : undefined
  const expected = item.expected ? `${item.expected.operator ? `${item.expected.operator} ` : ''}${item.expected.value}${item.expected.unit ? ` ${item.expected.unit}` : ''}` : undefined
  return <li className={`notification-row ${item.kind} severity-${item.severity}`}>
    <button type="button" className="notification-summary" aria-expanded={open} onClick={onToggle}>
      <span className={`severity ${item.severity}`}>{statusLabel(item.severity)}</span>
      <span className="notification-summary-copy"><strong>{item.title}</strong><small>{item.affectedResource?.name} · {statusLabel(item.status)}</small><span>{item.message}</span>{(observed || expected) && <span className="observed-summary">{observed ?? 'No observed value'}{expected ? ` · expected ${expected}` : ''}</span>}<small title={item.lastSeenAt ? dateTime(item.lastSeenAt) : undefined}>{item.lastSeenAt ? `Updated ${relativeTime(item.lastSeenAt)}` : 'Update time unavailable'} · observed {item.occurrenceCount} {item.occurrenceCount === 1 ? 'time' : 'times'}</small></span>
      {open ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
    </button>
    {open && <div className="notification-details">
      <Detail title="What happened"><p>{item.message}</p></Detail>
      <dl className="notification-facts"><div><dt>Affected item</dt><dd>{item.affectedResource?.name ?? 'Power Monitor'}</dd></div>{observed && <div><dt>{item.observed?.label}</dt><dd>{observed}</dd></div>}{expected && <div><dt>{item.expected?.label}</dt><dd>{expected}</dd></div>}<div><dt>First seen</dt><dd>{dateTime(item.openedAt)}</dd></div><div><dt>Last update</dt><dd>{dateTime(item.lastSeenAt)}</dd></div></dl>
      {item.evidence.length > 0 && <Detail title="Evidence"><dl className="notification-evidence">{item.evidence.map((evidence) => <div key={`${evidence.label}-${evidence.value}`}><dt>{evidence.label}</dt><dd className={evidence.status}>{evidence.value}</dd></div>)}</dl></Detail>}
      <Detail title="Why it matters"><p>{item.impact}</p></Detail>
      {item.remediation.automaticRecovery && <Detail title="What Power Monitor is doing"><p>{item.remediation.automaticRecovery}</p></Detail>}
      <Detail title="How to fix it"><p>{item.remediation.summary}</p><ol>{item.remediation.steps.map((step) => <li key={step}>{step}</li>)}</ol></Detail>
      {item.delivery?.attempted && <Detail title="Delivery status"><p>{item.delivery.channelName ?? 'Notification channel'}: {statusLabel(item.delivery.lastOutcome ?? 'unknown')}</p>{item.delivery.safeErrorSummary && <p>{item.delivery.safeErrorSummary} <code>{item.delivery.safeErrorCode}</code></p>}{item.delivery.retryAt && <p>Next retry: {dateTime(item.delivery.retryAt)}</p>}</Detail>}
      <details><summary>Technical details</summary><dl className="notification-evidence"><div><dt>Code</dt><dd>{item.code}</dd></div><div><dt>Notification ID</dt><dd>{item.id}</dd></div>{item.cause && <div><dt>Condition</dt><dd>{item.cause.explanation}</dd></div>}</dl></details>
      <div className="notification-actions">
        {canAcknowledge && item.kind === 'operational_alert' && item.status === 'open' && <button type="button" className="button secondary compact" disabled={acknowledge.isPending} onClick={() => { acknowledge.mutate() }}><Check size={15} /> Acknowledge</button>}
        {canAcknowledge && item.kind === 'operational_alert' && item.status !== 'resolved' && item.status !== 'silenced' && <button type="button" className="button secondary compact" onClick={onSilence}><Clock3 size={15} /> Silence</button>}
        {canAcknowledge && item.status === 'silenced' && <button type="button" className="button secondary compact" disabled={endSilence.isPending} onClick={() => { endSilence.mutate() }}>End silence</button>}
        {item.remediation.action && <button type="button" className="button primary compact" onClick={() => { onClose(); navigate(item.remediation.action?.target ?? '/') }}>{item.remediation.action.label}<ExternalLink size={14} /></button>}
        {canManageDelivery && item.suppression.permanentlySuppressible && <button type="button" className="button secondary compact" onClick={onSuppress}><EyeOff size={15} /> Do not remind me again</button>}
        {item.suppression.dismissible && <button type="button" className="button danger compact" onClick={onRemove}><Trash2 size={15} /> {item.status === 'resolved' ? 'Clear' : 'Remove'}</button>}
      </div>
    </div>}
  </li>
}

function Detail({ title, children }: { title: string; children: ReactNode }) { return <section className="notification-detail-section"><h4>{title}</h4>{children}</section> }

function SilenceDialog({ notification, onClose }: { notification: AlertSummary; onClose: () => void }) {
  const client = useQueryClient()
  const [duration, setDuration] = useState('60')
  const [customUntil, setCustomUntil] = useState('')
  const [customValid, setCustomValid] = useState(false)
  const [note, setNote] = useState('')
  const firstRef = useRef<HTMLSelectElement>(null)
  useEffect(() => { firstRef.current?.focus() }, [])
  const validUntil = duration !== 'custom' || customValid
  const save = useMutation({ mutationFn: () => {
    const until = duration === 'custom' ? new Date(customUntil) : new Date()
    if (duration !== 'custom') until.setMinutes(until.getMinutes() + Number(duration))
    return request(`/api/v1/notifications/${encodeURIComponent(notification.id)}/silence`, { method: 'POST', body: JSON.stringify({ until: until.toISOString(), note }) })
  }, onSuccess: async () => { await client.invalidateQueries({ queryKey: ['alerts'] }); onClose() } })
  return <div className="modal-backdrop"><form className="modal-card small-modal" role="dialog" aria-modal="true" aria-labelledby="silence-title" onSubmit={(event) => { event.preventDefault(); if (validUntil) save.mutate() }}><header><div><small>Temporary delivery pause</small><h2 id="silence-title">Silence {notification.title}</h2></div><button type="button" className="icon-button" aria-label="Close silence dialog" onClick={onClose}><X /></button></header><div className="setup-body form-grid single"><p>The active issue stays visible. Only repeated external delivery pauses until the selected time.</p><label>Duration<select ref={firstRef} value={duration} onChange={(event) => { setDuration(event.target.value) }}><option value="15">15 minutes</option><option value="60">1 hour</option><option value="240">4 hours</option><option value="1440">Until tomorrow</option><option value="custom">Custom date and time</option></select></label>{duration === 'custom' && <label>Silence until<input type="datetime-local" value={customUntil} onChange={(event) => { const value = event.target.value; setCustomUntil(value); setCustomValid(new Date(value).getTime() > Date.now()) }} required /></label>}<label>Reason (optional)<textarea value={note} onChange={(event) => { setNote(event.target.value) }} /></label>{save.error && <p className="error-text">{save.error.message}</p>}</div><footer><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button type="submit" className="button primary" disabled={!validUntil || save.isPending}>Silence temporarily</button></footer></form></div>
}

function SuppressDialog({ notification, onClose }: { notification: AlertSummary; onClose: () => void }) {
  const client = useQueryClient()
  const [scope, setScope] = useState<'user' | 'home'>('home')
  const [reason, setReason] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const firstRef = useRef<HTMLInputElement>(null)
  useEffect(() => { firstRef.current?.focus() }, [])
  const save = useMutation({ mutationFn: () => request(`/api/v1/notifications/${encodeURIComponent(notification.id)}/suppress`, { method: 'POST', body: JSON.stringify({ scope, reason, confirmed }) }), onSuccess: async () => { await client.invalidateQueries({ queryKey: ['alerts'] }); await client.invalidateQueries({ queryKey: ['notification-suppressions'] }); onClose() } })
  return <div className="modal-backdrop"><form className="modal-card small-modal" role="dialog" aria-modal="true" aria-labelledby="suppress-title" onSubmit={(event) => { event.preventDefault(); save.mutate() }}><header><div><small>Optional recommendation</small><h2 id="suppress-title">Stop email setup reminders?</h2></div><button type="button" className="icon-button" aria-label="Close ignore dialog" onClick={onClose}><X /></button></header><div className="setup-body form-grid single"><p>Power Monitor will continue showing alerts in the dashboard, but no email will be sent until a delivery channel is configured.</p><fieldset><legend>Reminder scope</legend><label><input ref={firstRef} type="radio" name="scope" checked={scope === 'home'} onChange={() => { setScope('home') }} /> Do not remind this home again</label><label><input type="radio" name="scope" checked={scope === 'user'} onChange={() => { setScope('user') }} /> Dismiss for me</label></fieldset><label>Reason (optional)<textarea value={reason} onChange={(event) => { setReason(event.target.value) }} /></label><label className="check-row"><input type="checkbox" checked={confirmed} onChange={(event) => { setConfirmed(event.target.checked) }} /> I understand dashboard alerts continue and I can restore this reminder from Settings.</label>{save.error && <p className="error-text">{save.error.message}</p>}</div><footer><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button type="submit" className="button primary" disabled={!confirmed || save.isPending}>Do not remind me again</button></footer></form></div>
}

function RemoveNotificationDialog({ notification, onClose }: { notification: AlertSummary; onClose: () => void }) {
  const client = useQueryClient()
  const firstRef = useRef<HTMLButtonElement>(null)
  const resolved = notification.status === 'resolved'
  useEffect(() => { firstRef.current?.focus() }, [])
  const remove = useMutation({
    mutationFn: () => request(`/api/v1/notifications/${encodeURIComponent(notification.id)}/dismiss`, { method: 'POST' }),
    onMutate: async () => {
      await client.cancelQueries({ queryKey: ['alerts'] })
      const previous = client.getQueriesData<NotificationPageCache>({ queryKey: ['alerts'] })
      client.setQueriesData<NotificationPageCache>({ queryKey: ['alerts'] }, (current) => (
        removeCachedNotification(current, notification.id)
      ))
      return { previous }
    },
    onError: (_error, _variables, context) => {
      context?.previous.forEach(([queryKey, value]) => {
        client.setQueryData(queryKey, value)
      })
    },
    onSuccess: () => {
      onClose()
    },
    onSettled: async () => client.invalidateQueries({ queryKey: ['alerts'] }),
  })
  return <div className="modal-backdrop"><div className="modal-card small-modal" role="dialog" aria-modal="true" aria-labelledby="remove-notification-title"><header><div><small>Notification center</small><h2 id="remove-notification-title">{resolved ? 'Clear this resolved notification?' : 'Remove this notification?'}</h2></div><button type="button" className="icon-button" aria-label="Close remove dialog" onClick={onClose}><X /></button></header><div className="setup-body form-grid single"><p><strong>{notification.title}</strong> will be {resolved ? 'cleared from Recently resolved' : 'removed from your notification center'}.</p><p>Monitoring, alert rules, delivery history, and the audit record remain active. If the condition changes or happens again, a new update will appear.</p>{remove.error && <p className="error-text">{remove.error.message}</p>}</div><footer><button ref={firstRef} type="button" className="button secondary" onClick={onClose}>Cancel</button><button type="button" className="button danger" disabled={remove.isPending} onClick={() => { remove.mutate() }}><Trash2 size={15} /> {remove.isPending ? (resolved ? 'Clearing…' : 'Removing…') : (resolved ? 'Clear notification' : 'Remove notification')}</button></footer></div></div>
}

function ClearResolvedNotificationsDialog({ notifications, onClose }: { notifications: AlertSummary[]; onClose: () => void }) {
  const client = useQueryClient()
  const firstRef = useRef<HTMLButtonElement>(null)
  useEffect(() => { firstRef.current?.focus() }, [])
  const clear = useMutation({
    mutationFn: async () => {
      for (let start = 0; start < notifications.length; start += 10) {
        const batch = notifications.slice(start, start + 10)
        await Promise.all(batch.map((notification) => (
          request(`/api/v1/notifications/${encodeURIComponent(notification.id)}/dismiss`, { method: 'POST' })
        )))
      }
    },
    onMutate: async () => {
      await client.cancelQueries({ queryKey: ['alerts'] })
      const previous = client.getQueriesData<NotificationPageCache>({ queryKey: ['alerts'] })
      client.setQueriesData<NotificationPageCache>({ queryKey: ['alerts'] }, (current) => (
        notifications.reduce(
          (page, notification) => removeCachedNotification(page, notification.id),
          current,
        )
      ))
      return { previous }
    },
    onError: (_error, _variables, context) => {
      context?.previous.forEach(([queryKey, value]) => {
        client.setQueryData(queryKey, value)
      })
    },
    onSuccess: onClose,
    onSettled: async () => client.invalidateQueries({ queryKey: ['alerts'] }),
  })
  return <div className="modal-backdrop"><div className="modal-card small-modal" role="dialog" aria-modal="true" aria-labelledby="clear-resolved-title"><header><div><small>Notification center</small><h2 id="clear-resolved-title">Clear all resolved notifications?</h2></div><button type="button" className="icon-button" aria-label="Close clear resolved dialog" onClick={onClose}><X /></button></header><div className="setup-body form-grid single"><p>This clears <strong>{notifications.length}</strong> resolved {notifications.length === 1 ? 'notification' : 'notifications'} from your notification center.</p><p>Monitoring, delivery history, and audit records remain intact. A genuinely new occurrence will appear normally.</p>{clear.error && <p className="error-text">{clear.error.message}</p>}</div><footer><button ref={firstRef} type="button" className="button secondary" onClick={onClose}>Cancel</button><button type="button" className="button danger" disabled={clear.isPending} onClick={() => { clear.mutate() }}><Trash2 size={15} /> {clear.isPending ? 'Clearing…' : 'Clear all resolved'}</button></footer></div></div>
}
