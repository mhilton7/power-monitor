import { AlertTriangle, CheckCircle2, LoaderCircle, RefreshCw } from 'lucide-react'
import { errorMessage } from '../../api/client'
import type { ReactNode } from 'react'

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="state-block loading-state" role="status">
      <LoaderCircle className="spin" aria-hidden="true" />
      <span>{label}</span>
    </div>
  )
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  return (
    <div className="state-block error-state" role="alert">
      <AlertTriangle aria-hidden="true" />
      <div>
        <strong>Something needs attention</strong>
        <p>{errorMessage(error)}</p>
        {retry && (
          <button type="button" className="button secondary" onClick={retry}>
            <RefreshCw size={16} /> Try again
          </button>
        )}
      </div>
    </div>
  )
}

export function EmptyState({
  title,
  message,
  action,
  compact = false,
}: {
  title: string
  message: string
  action?: ReactNode
  compact?: boolean
}) {
  return (
    <div className={`state-block empty-state ${compact ? 'compact' : ''}`}>
      <CheckCircle2 aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <p>{message}</p>
        {action}
      </div>
    </div>
  )
}

export function InlineNotice({
  tone = 'info',
  children,
}: {
  tone?: 'info' | 'success' | 'warning' | 'danger'
  children: ReactNode
}) {
  return <div className={`inline-notice ${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>{children}</div>
}
