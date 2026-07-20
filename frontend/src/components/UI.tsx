import { useEffect, type ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, LoaderCircle, RefreshCw } from 'lucide-react'

export function Panel({
  title,
  eyebrow,
  actions,
  children,
  className = '',
}: {
  title?: string
  eyebrow?: string
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`panel ${className}`}>
      {(title ?? eyebrow ?? actions) && (
        <header className="panel-header">
          <div>
            {eyebrow && <span className="eyebrow">{eyebrow}</span>}
            {title && <h2>{title}</h2>}
          </div>
          {actions && <div className="panel-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  )
}

export function Metric({ label, value, unit, detail }: { label: string; value: ReactNode; unit?: string; detail?: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>
        {value} {unit && <small>{unit}</small>}
      </strong>
      {detail && <em>{detail}</em>}
    </div>
  )
}

const statusTone = (status: string) => {
  if (['online_synchronized', 'applied', 'healthy', 'completed', 'validated'].includes(status)) return 'good'
  if (['online_with_backlog', 'online_push_only', 'pending', 'scheduled', 'acknowledged'].includes(status)) return 'warn'
  if (['offline_last_known', 'critical', 'failed', 'revoked', 'rejected'].includes(status)) return 'bad'
  return 'neutral'
}

export function StatusPill({ status, label }: { status: string; label?: string }) {
  const text = label ?? status.replaceAll('_', ' ')
  return (
    <span className={`status status-${statusTone(status)}`}>
      <span aria-hidden="true" className="status-dot" />
      {text}
    </span>
  )
}

export function PageTitle({ eyebrow, title, description, actions }: { eyebrow: string; title: string; description?: string; actions?: ReactNode }) {
  useEffect(() => {
    document.title = `${title} · Power Monitor`
  }, [title])
  return (
    <header className="page-title">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  )
}

export function LoadingState({ label = 'Loading current data…' }: { label?: string }) {
  return (
    <div className="state-card" role="status">
      <LoaderCircle className="spin" aria-hidden="true" />
      <p>{label}</p>
    </div>
  )
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const message = error instanceof Error ? error.message : 'The request could not be completed.'
  return (
    <div className="state-card state-error" role="alert">
      <AlertTriangle aria-hidden="true" />
      <div>
        <strong>Something needs attention</strong>
        <p>{message}</p>
      </div>
      {retry && (
        <button className="button secondary" onClick={retry}>
          <RefreshCw size={16} /> Retry
        </button>
      )}
    </div>
  )
}

export function EmptyState({ title, message, action }: { title: string; message: string; action?: ReactNode }) {
  return (
    <div className="state-card state-empty">
      <CheckCircle2 aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <p>{message}</p>
      </div>
      {action}
    </div>
  )
}

export function Disclosure() {
  return (
    <aside className="disclosure" aria-label="Cost estimate disclosure">
      <AlertTriangle size={18} aria-hidden="true" />
      <p>
        <strong>Estimate, not utility bill.</strong> Configured monitored energy may differ because of meter accuracy,
        missing circuits, baseline allocation, provider differences, taxes, credits, rounding, and tariff changes.
      </p>
    </aside>
  )
}

export const formatNumber = (value: string | number | undefined, maximumFractionDigits = 1) =>
  new Intl.NumberFormat(undefined, { maximumFractionDigits }).format(Number(value ?? 0))

export const formatMoney = (value: string | number | undefined) =>
  new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(Number(value ?? 0))

export const formatTime = (value: string | undefined) =>
  value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : 'Never'
