import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Clock3,
  Settings2,
  X,
} from 'lucide-react'
import { useState } from 'react'
import { Link } from '../../app/router'
import { ModalLayer } from '../../components/overlays/ModalLayer'
import type { ConfigurationState, ConfigurationStatus } from '../../types/models'

function icon(state: ConfigurationState) {
  if (state === 'ready') return <CheckCircle2 aria-hidden="true" />
  if (state === 'waiting_for_data') return <Clock3 aria-hidden="true" />
  if (state === 'error') return <CircleAlert aria-hidden="true" />
  return <AlertTriangle aria-hidden="true" />
}

export function ConfigurationStatusChip({
  status,
  className = '',
}: {
  status?: ConfigurationStatus
  className?: string
}) {
  const [open, setOpen] = useState(false)
  if (!status) return null
  return (
    <>
      <button
        type="button"
        className={`configuration-status-chip ${status.state} ${className}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => { setOpen(true) }}
      >
        {icon(status.state)}
        <span>{status.label}</span>
      </button>
      {open && (
        <ModalLayer onRequestClose={() => { setOpen(false) }}>
          <ConfigurationStatusDialog status={status} onClose={() => { setOpen(false) }} />
        </ModalLayer>
      )}
    </>
  )
}

function ConfigurationStatusDialog({
  status,
  onClose,
}: {
  status: ConfigurationStatus
  onClose: () => void
}) {
  return (
    <section
      className="modal-card configuration-status-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="configuration-status-title"
    >
      <header>
        <div>
          <small>Configuration status</small>
          <h2 id="configuration-status-title">{status.label}</h2>
          <p>{status.summary}</p>
        </div>
        <button type="button" className="icon-button" aria-label="Close configuration status" onClick={onClose}>
          <X />
        </button>
      </header>
      <div className="configuration-issue-list">
        {status.issues.length === 0 ? (
          <div className="configuration-ready">
            <CheckCircle2 aria-hidden="true" />
            <div><strong>Configuration is complete</strong><p>Monitoring and billing are ready. Waiting for new sensor data is normal.</p></div>
          </div>
        ) : status.issues.map((issue) => (
          <article key={issue.id} className={`configuration-issue ${issue.state}`}>
            <div className="configuration-issue-icon">{icon(issue.state)}</div>
            <div>
              <div className="configuration-issue-heading">
                <h3>{issue.title}</h3>
                <span className={`pill ${issue.blocking ? 'warning' : ''}`}>{issue.blocking ? 'Blocking' : 'Advisory'}</span>
              </div>
              <dl>
                <div><dt>What is wrong</dt><dd>{issue.whatIsWrong}</dd></div>
                <div><dt>Why it matters</dt><dd>{issue.whyItMatters}</dd></div>
                <div><dt>How to fix it</dt><dd>{issue.howToFix}</dd></div>
              </dl>
              <Link className="button secondary compact" to={issue.action.target} onClick={onClose}>
                <Settings2 size={16} /> {issue.action.label} <ChevronRight size={15} />
              </Link>
            </div>
          </article>
        ))}
      </div>
      <footer>
        <button type="button" className="button secondary" onClick={onClose}>Close</button>
      </footer>
    </section>
  )
}
