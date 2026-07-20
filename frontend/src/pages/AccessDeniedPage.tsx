import { LockKeyhole } from 'lucide-react'
import { Link } from 'react-router-dom'
import { PageTitle, Panel } from '../components/UI'

export function AccessDeniedPage({ permission }: { permission?: string }) {
  return (
    <>
      <PageTitle eyebrow="Access control" title="Access denied" description="Your account does not have permission to open this workspace." />
      <Panel className="access-denied-panel">
        <div className="state-card state-error" role="alert">
          <LockKeyhole aria-hidden="true" />
          <div>
            <strong>This page is restricted</strong>
            <p>Ask an administrator to review your roles and site access.{permission ? ` Required permission: ${permission}.` : ''}</p>
          </div>
          <Link className="button secondary" to="/">Return to Overview</Link>
        </div>
      </Panel>
    </>
  )
}
