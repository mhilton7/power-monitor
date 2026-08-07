import {
  Check,
  Clock3,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
  X,
} from 'lucide-react'
import { InlineNotice } from '../../components/feedback/States'
import type {
  FirmwareDeploymentSummary,
  FirmwareVerificationCheck,
} from '../../types/models'
import { dateTime, statusLabel } from '../../utils/format'

export const firmwareTerminalStates = new Set(['completed', 'failed', 'rolled_back', 'cancelled'])
export const firmwareRetryableStates = new Set(['failed', 'rolled_back', 'cancelled'])
export const firmwareCancellableStates = new Set([
  'waiting_canary',
  'scheduled',
  'offered',
  'manifest_authenticated',
  'download_started',
  'downloading',
  'binary_verified',
])

const attentionStates = new Set(['failed', 'rollback_detected', 'rolled_back'])

function pendingCheck(deployment: FirmwareDeploymentSummary, key: string): boolean {
  return deployment.verification?.checks.some((check) => check.key === key && check.status === 'pending') ?? false
}

export function firmwareDeploymentLabel(deployment: FirmwareDeploymentSummary): string {
  switch (deployment.state) {
    case 'waiting_canary': return 'Waiting for sensor'
    case 'scheduled':
    case 'offered': return 'Waiting for sensor'
    case 'manifest_authenticated': return 'Preparing download'
    case 'download_started':
    case 'downloading': return 'Downloading'
    case 'binary_verified': return 'Verifying'
    case 'partition_written': return 'Writing partition'
    case 'rebooting': return 'Rebooting'
    case 'post_boot_validation': return 'Validating locally'
    case 'validated': return 'Waiting for server stabilization'
    case 'waiting_for_heartbeat':
    case 'awaiting_heartbeat':
      return pendingCheck(deployment, 'post_update_reading')
        ? 'Waiting for first reading'
        : 'Waiting for server stabilization'
    case 'completed': return 'Completed'
    case 'failed': return 'Failed'
    case 'rollback_detected':
    case 'rolled_back': return 'Rolled back'
    case 'cancelled': return 'Cancelled'
    default: return statusLabel(deployment.displayState ?? deployment.state)
  }
}

function valueAt(source: Record<string, unknown> | undefined, key: string): unknown {
  if (!source) return undefined
  return source[key]
}

function evidenceText(value: unknown): string | undefined {
  if (typeof value === 'string' && value.trim()) return value
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return undefined
}

function recoveryEvidence(deployment: FirmwareDeploymentSummary): Record<string, unknown> | undefined {
  const value = valueAt(deployment.interruptionEvidence, 'ota_recovery')
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined
}

function previousBootStage(deployment: FirmwareDeploymentSummary): string | undefined {
  const recovery = recoveryEvidence(deployment)
  return deployment.verification?.previousBootStage
    ?? evidenceText(valueAt(recovery, 'previous_boot_stage'))
    ?? evidenceText(valueAt(deployment.interruptionEvidence, 'last_state'))
}

function previousResetReason(deployment: FirmwareDeploymentSummary): string | undefined {
  const recovery = recoveryEvidence(deployment)
  return deployment.verification?.previousResetReason
    ?? evidenceText(valueAt(recovery, 'previous_reset_reason'))
    ?? evidenceText(valueAt(recovery, 'reset_reason'))
}

function targetBootDetail(deployment: FirmwareDeploymentSummary): string {
  if (deployment.verification?.targetBootIdObserved) return deployment.verification.targetBootIdObserved
  const check = deployment.verification?.checks.find((item) => item.key === 'target_boot')
  return check?.detail ?? 'Not observed yet'
}

function rollbackState(deployment: FirmwareDeploymentSummary): string {
  if (deployment.verification?.rollbackState) return deployment.verification.rollbackState
  if (deployment.rollbackVersion) return `Rolled back to ${deployment.rollbackVersion}`
  if (deployment.state === 'rollback_detected' || deployment.state === 'rolled_back') return 'Rollback detected'
  return 'No rollback reported'
}

function CheckIcon({ check }: { check: FirmwareVerificationCheck }) {
  if (check.status === 'passed') return <Check aria-hidden="true" />
  if (check.status === 'failed') return <TriangleAlert aria-hidden="true" />
  if (check.status === 'unavailable') return <X aria-hidden="true" />
  return <Clock3 aria-hidden="true" />
}

function VerificationChecklist({ deployment }: { deployment: FirmwareDeploymentSummary }) {
  const verification = deployment.verification
  const failureCode = verification?.exactFailureCode ?? deployment.failureCode
  const targetVersion = verification?.targetVersionObserved ?? deployment.validatedVersion
  const targetBuildHash = verification?.targetBuildHashObserved ?? deployment.validatedBuildHash
  const bootStage = previousBootStage(deployment)
  const resetReason = previousResetReason(deployment)
  const allChecksPassed = verification?.checks.length
    ? verification.checks.every((check) => check.status === 'passed')
    : false

  if (!verification) {
    return (
      <InlineNotice tone={attentionStates.has(deployment.state) ? 'danger' : 'warning'}>
        <strong>{deployment.failureCode ?? 'Legacy deployment evidence'}</strong>{' '}
        {deployment.failureSummary ?? 'This deployment predates the authoritative verification checklist.'}{' '}
        {deployment.rollbackVersion
          ? `Previous firmware ${deployment.rollbackVersion} was restored. `
          : ''}
        Retry starts a fully evidenced attempt.
      </InlineNotice>
    )
  }

  return (
    <section className="firmware-verification-status" aria-labelledby={`firmware-verification-${deployment.id}`}>
      <div className="firmware-verification-status-heading">
        <ShieldCheck aria-hidden="true" />
        <div>
          <strong id={`firmware-verification-${deployment.id}`}>Post-update verification</strong>
          <small>Server-authoritative checks from signed sensor evidence</small>
        </div>
      </div>

      {verification.blocker ? (
        <InlineNotice tone={firmwareTerminalStates.has(deployment.state) || verification.blocker.action === 'retry' ? 'danger' : 'warning'}>
          <strong>{verification.blocker.title}</strong>{' '}{verification.blocker.detail}{' '}
          <code>{verification.blocker.code}</code>
        </InlineNotice>
      ) : allChecksPassed ? (
        <InlineNotice tone="success"><Check aria-hidden="true" /> All required post-update checks passed.</InlineNotice>
      ) : (
        <InlineNotice tone="warning">
          This terminal deployment predates complete retained verification evidence. Review the unavailable checks below; Retry starts a fully evidenced attempt.
        </InlineNotice>
      )}

      <ul className="firmware-checklist" aria-label="Post-update verification checklist">
        {verification.checks.map((check) => (
          <li key={check.key} className={`firmware-check ${check.status}`}>
            <CheckIcon check={check} />
            <span>
              <strong>{check.label}</strong>
              <small>{check.detail}{check.observedAt ? ` · ${dateTime(check.observedAt)}` : ''}</small>
            </span>
            <span className={`pill ${check.status === 'passed' ? 'success' : check.status === 'failed' ? 'danger' : ''}`}>
              {statusLabel(check.status)}
            </span>
          </li>
        ))}
      </ul>

      <dl className="firmware-verification-evidence">
        <div><dt>Target version expected</dt><dd>{verification.targetVersionExpected ?? deployment.targetVersion ?? 'Unavailable'}</dd></div>
        <div><dt>Target version observed</dt><dd>{targetVersion ?? 'Not observed yet'}</dd></div>
        <div><dt>Target build hash expected</dt><dd>{verification.targetBuildHashExpected ?? deployment.targetBuildHash ?? 'Unavailable'}</dd></div>
        <div><dt>Target build hash observed</dt><dd>{targetBuildHash ?? 'Not observed yet'}</dd></div>
        <div><dt>Target boot ID observed</dt><dd>{targetBootDetail(deployment)}</dd></div>
        <div><dt>Verification heartbeats</dt><dd>{verification.verificationHeartbeatCount}</dd></div>
        <div><dt>Required heartbeats</dt><dd>{verification.verificationHeartbeatRequired ?? 'Unavailable'}</dd></div>
        <div><dt>Stabilization elapsed</dt><dd>{verification.stabilizationElapsedSeconds} of {verification.stabilizationRequiredSeconds} seconds</dd></div>
        <div><dt>Post-update reading</dt><dd>{deployment.readingConfirmedAt ? dateTime(deployment.readingConfirmedAt) : 'Not received yet'}</dd></div>
        <div><dt>Blocking critical alerts</dt><dd>{verification.blockingCriticalAlertCount}</dd></div>
        <div><dt>Last sensor activity</dt><dd>{dateTime(verification.lastSensorActivityAt)}</dd></div>
        <div><dt>Last OTA report</dt><dd>{dateTime(verification.lastReportAt ?? deployment.lastReportAt)}</dd></div>
        <div><dt>Previous boot stage</dt><dd>{bootStage ? statusLabel(bootStage) : 'Not reported'}</dd></div>
        <div><dt>Previous reset reason</dt><dd>{resetReason ? statusLabel(resetReason) : 'Not reported'}</dd></div>
        <div><dt>Rollback state</dt><dd>{rollbackState(deployment)}</dd></div>
        <div><dt>Exact failure code</dt><dd><code>{failureCode ?? 'None reported'}</code></dd></div>
      </dl>
    </section>
  )
}

function DeploymentTimeline({ deployment }: { deployment: FirmwareDeploymentSummary }) {
  const milestones = [
    ['Deployment created', deployment.createdAt ?? deployment.scheduledAt],
    ['Scheduled', deployment.scheduledAt],
    ['Download completed', deployment.downloadedAt],
    ['Installed', deployment.installedAt],
    ['Locally validated', deployment.validatedAt],
    ['Last OTA report', deployment.lastReportAt],
    ['State changed', deployment.stateChangedAt],
    ['Terminal outcome', deployment.terminalAt],
  ].filter((item): item is [string, string] => Boolean(item[1]))

  if (!milestones.length) return null
  return (
    <details className="firmware-activity-timeline">
      <summary>Activity timeline</summary>
      <ol>
        {milestones.map(([label, at], index) => <li key={`${label}-${at}-${index}`}><span>{label}</span><time dateTime={at}>{dateTime(at)}</time></li>)}
      </ol>
    </details>
  )
}

export function FirmwareDeploymentStatus({
  deployment,
  compact = false,
}: {
  deployment: FirmwareDeploymentSummary
  compact?: boolean
}) {
  const terminal = firmwareTerminalStates.has(deployment.state)
  const attention = attentionStates.has(deployment.state)
  const complete = deployment.state === 'completed'
  const stopped = deployment.state === 'cancelled'
  const determinate = deployment.progressMode === 'determinate'
  const progress = Math.max(0, Math.min(100, deployment.progress))
  const label = firmwareDeploymentLabel(deployment)
  const activityAt = deployment.verification?.lastSensorActivityAt
    ?? deployment.lastReportAt
    ?? deployment.downloadedAt
    ?? deployment.scheduledAt

  const content = (
    <div className={`firmware-progress ${attention ? 'failed' : complete ? 'complete' : ''} ${compact ? 'compact' : ''}`} aria-live="polite">
      <div className="firmware-progress-heading">
        <span>
          {complete ? <Check aria-hidden="true" />
            : attention ? <TriangleAlert aria-hidden="true" />
              : stopped ? <X aria-hidden="true" />
                : <RefreshCw className={terminal ? undefined : 'spin'} aria-hidden="true" />}
        </span>
        <div><strong>{label}</strong><small>Attempt {deployment.attempt} · revision {deployment.revision}</small></div>
        <span>{determinate ? `${progress}%` : terminal ? statusLabel(deployment.state) : 'In progress'}</span>
      </div>
      {determinate ? <progress max={100} value={progress} /> : !terminal ? <progress max={100} /> : null}
      {!determinate && !terminal && <small>Waiting for the next authenticated sensor progress report.</small>}
      {activityAt && <small>Last sensor activity {dateTime(activityAt)}</small>}
      {deployment.bytesReceived > 0 && <small>{new Intl.NumberFormat().format(deployment.bytesReceived)} bytes received</small>}
      <VerificationChecklist deployment={deployment} />
      <DeploymentTimeline deployment={deployment} />
    </div>
  )

  if (!compact) return content
  return (
    <details className="firmware-deployment-details" open={!terminal}>
      <summary>Verification and activity</summary>
      {content}
    </details>
  )
}
