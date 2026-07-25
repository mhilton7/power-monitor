import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Bell, Check, X } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { request } from '../../api/client'
import { dateTime, statusLabel } from '../../utils/format'
import type { AlertSummary } from '../../types/models'
import { EmptyState } from '../../components/feedback/States'

export function AlertDrawer({
  open,
  alerts,
  onClose,
}: {
  open: boolean
  alerts: AlertSummary[]
  onClose: () => void
}) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const client = useQueryClient()
  const acknowledge = useMutation({
    mutationFn: (id: string) =>
      request(`/api/v1/alerts/${id}/acknowledge`, {
        method: 'POST',
        body: JSON.stringify({ note: 'Acknowledged from Single Home alerts' }),
      }),
    onSuccess: async () => client.invalidateQueries({ queryKey: ['alerts'] }),
  })

  useEffect(() => {
    if (!open) return
    closeRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => { document.removeEventListener('keydown', onKeyDown); }
  }, [onClose, open])

  if (!open) return null
  return (
    <>
      <button type="button" className="drawer-backdrop" aria-label="Close alerts" onClick={onClose} />
      <aside className="alert-drawer" role="dialog" aria-modal="true" aria-labelledby="alert-drawer-title">
        <header>
          <div>
            <span className="icon-tile"><Bell aria-hidden="true" /></span>
            <div>
              <h2 id="alert-drawer-title">Alerts</h2>
              <p>Active items that may need attention.</p>
            </div>
          </div>
          <button ref={closeRef} type="button" className="icon-button" aria-label="Close alerts" onClick={onClose}>
            <X />
          </button>
        </header>
        <div className="drawer-content">
          {alerts.length === 0 ? (
            <EmptyState title="Everything looks calm" message="There are no active alerts for your home." />
          ) : (
            <ul className="alert-list">
              {alerts.map((alert) => (
                <li key={alert.id}>
                  <div>
                    <span className={`severity ${alert.severity}`}>{statusLabel(alert.severity)}</span>
                    <strong>{alert.title}</strong>
                    <p>{alert.message}</p>
                    <small>{dateTime(alert.openedAt)}</small>
                  </div>
                  <button
                    type="button"
                    className="button secondary compact"
                    disabled={acknowledge.isPending}
                    onClick={() => { acknowledge.mutate(alert.id); }}
                  >
                    <Check size={15} /> Acknowledge
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>
    </>
  )
}
