import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle2,
  Circle,
  Clock3,
  DatabaseBackup,
  HardDrive,
  LockKeyhole,
  Radio,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Trash2,
  XCircle,
} from 'lucide-react'
import { useEffect, useState } from 'react'

import { ApiError, errorMessage, json, request } from '../../api/client'
import { Surface } from '../../components/data-display/Surface'
import { ErrorState, InlineNotice, LoadingState } from '../../components/feedback/States'
import { ProtectedChangeDialog } from '../../components/security/ProtectedChangeDialog'
import { dateTime, relativeTime } from '../../utils/format'
import { adaptDataResetOperation, adaptDataResetPlan } from './adapters'
import type {
  DataResetBackupMode,
  DataResetCategory,
  DataResetOperation,
  DataResetOperationState,
  DataResetPlan,
  DataResetResumeRecord,
  DisconnectedSensorPolicy,
} from './types'

const RESET_CATEGORIES: Array<{ id: DataResetCategory; label: string; description: string }> = [
  { id: 'measurement_history', label: 'Electrical readings, power, energy, and coverage history', description: 'Required. Clears server readings, intervals, rollups, sensor microSD reading files, indexes, and backlogs.' },
  { id: 'cost_history', label: 'Cost, tier, and billing-cycle accumulated usage', description: 'Clears historical calculations and restarts cost and tier accumulation from zero.' },
  { id: 'pricing_history', label: 'Historical pricing versions and rate-source checks', description: 'Preserves the exact current effective pricing configuration and creates a clean baseline.' },
  { id: 'generated_outputs', label: 'History exports and generated reports', description: 'Permanently removes generated files that could retain cleared history.' },
]

const PRESERVED_LABELS: Record<string, string> = {
  users_roles_sessions_mfa: 'Users, roles, sessions, and MFA configuration',
  site_circuits_aggregates_devices: 'Home, circuits, aggregates, sensors, and assignments',
  device_uuid_credentials_network_configuration: 'Device UUIDs, credentials, Wi-Fi, static network settings, server URL, and CA trust',
  device_desired_effective_configuration: 'Desired and effective sensor, PZEM, CT, and timezone configuration',
  notification_and_smtp_configuration: 'Notification rules and SMTP configuration',
  firmware_ota_events_coredumps: 'Firmware, OTA recovery evidence, operational events, and coredumps',
  current_utility_accounts_and_active_pricing: 'Current utility accounts and exact effective pricing configuration',
}

const OPERATION_STAGE_ORDER: DataResetOperationState[] = [
  'preparing_sensors',
  'sensors_prepared',
  'backup_running',
  'backup_verified',
  'database_reset_running',
  'database_reset_committed',
  'sensor_commit_running',
  'verification_running',
  'completed_with_resets_pending_on_reconnect',
  'completed',
]

const PROGRESS_STEPS = [
  ['preparing_sensors', 'Preparing connected sensors'],
  ['preparing_sensors', 'Waiting for sensor receipts'],
  ['backup_running', 'Creating backup'],
  ['backup_running', 'Verifying backup'],
  ['database_reset_running', 'Clearing server readings'],
  ['database_reset_running', 'Clearing cost history'],
  ['database_reset_running', 'Resetting tier history'],
  ['database_reset_running', 'Creating new pricing baseline'],
  ['sensor_commit_running', 'Committing sensor resets'],
  ['verification_running', 'Verifying configuration preservation'],
  ['completed_with_resets_pending_on_reconnect', 'Waiting for disconnected sensors'],
  ['completed', 'Completed'],
] as const

type WizardPhase = 'scope' | 'plan' | 'backup' | 'confirm'
type ProtectedAction = 'execute' | 'retry' | 'cancel'

function operationStorageKey(siteId: string) {
  return `pm-data-reset-operation:${siteId}`
}

function readResumeRecord(siteId: string): DataResetResumeRecord | undefined {
  try {
    const value = localStorage.getItem(operationStorageKey(siteId))
    if (!value) return undefined
    const parsed = JSON.parse(value) as Partial<DataResetResumeRecord>
    if (typeof parsed.operationId !== 'string' || !parsed.operationId) return undefined
    return {
      operationId: parsed.operationId,
      participantNames: parsed.participantNames && typeof parsed.participantNames === 'object' ? parsed.participantNames : {},
      planCounts: parsed.planCounts && typeof parsed.planCounts === 'object' ? parsed.planCounts : {},
      categories: Array.isArray(parsed.categories) ? parsed.categories : [],
      preserved: Array.isArray(parsed.preserved) ? parsed.preserved : [],
    }
  } catch {
    return undefined
  }
}

function writeResumeRecord(siteId: string, plan: DataResetPlan, operationId: string) {
  const record: DataResetResumeRecord = {
    operationId,
    participantNames: Object.fromEntries(plan.participants.map((participant) => [participant.deviceId, participant.name])),
    planCounts: plan.counts,
    categories: plan.categories,
    preserved: plan.preserved,
  }
  localStorage.setItem(operationStorageKey(siteId), JSON.stringify(record))
  return record
}

function count(source: Record<string, number>, ...keys: string[]) {
  return keys.reduce((total, key) => total + (source[key] ?? 0), 0)
}

function numericRecord(value: unknown): Record<string, number> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return Object.fromEntries(Object.entries(value).filter((entry): entry is [string, number] => (
    typeof entry[1] === 'number' && Number.isFinite(entry[1]) && entry[1] >= 0
  )))
}

function objectRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function evidenceBoolean(evidence: Record<string, unknown>, key: string): boolean | undefined {
  return typeof evidence[key] === 'boolean' ? evidence[key] : undefined
}

function verificationStatus(
  evidence: Record<string, unknown>,
  statusKey: string,
  confirmedKey: string,
) {
  const status = evidence[statusKey]
  if (typeof status === 'string' && status) return statusText(status)
  return evidenceBoolean(evidence, confirmedKey) === true ? 'Confirmed' : 'Not yet confirmed'
}

function statusText(value: string) {
  return value.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase())
}

function stateTone(state: DataResetOperationState) {
  if (state === 'completed') return 'success'
  if (state === 'completed_with_resets_pending_on_reconnect') return 'warning'
  if (['partial_failure', 'attention_required', 'failed_before_commit'].includes(state)) return 'danger'
  if (state === 'cancelled') return 'neutral'
  return 'info'
}

function isTerminal(state: DataResetOperationState) {
  return ['completed', 'cancelled', 'failed_before_commit'].includes(state)
}

function shouldPoll(state?: DataResetOperationState) {
  if (!state || isTerminal(state)) return false
  return state === 'completed_with_resets_pending_on_reconnect' ? 15_000 : 2_000
}

function isProblem(error: unknown, code: string) {
  return error instanceof ApiError && error.problem.code === code
}

function DataResetError({ error }: { error: unknown }) {
  const code = error instanceof ApiError ? error.problem.code : undefined
  const guidance = code === 'data_reset_plan_expired' || code === 'data_reset_plan_stale'
    ? 'Generate a new dry-run plan and review its counts before trying again.'
    : code === 'reauthentication_required'
      ? 'Verify your password or MFA again, then resubmit the protected action.'
      : code === 'data_reset_backup_failed'
        ? 'No central deletion should occur until a verified backup succeeds. Review Data & Backups, then retry.'
        : code === 'data_reset_cancel_unsafe'
          ? 'The central commit boundary has passed. Cancellation is unsafe; use an idempotent retry instead.'
          : code === 'data_reset_historical_device_scope_unsafe'
            ? 'A sensor previously assigned here now belongs to another site. Resolve its device-wide SD backlog and assignment boundary before planning this reset.'
          : undefined
  return <InlineNotice tone="danger"><strong>{code ? `${code}: ` : ''}</strong>{errorMessage(error)}{guidance ? ` ${guidance}` : ''}</InlineNotice>
}

export function DataResetWorkflow({
  siteId,
  siteName,
  mfaEnabled,
}: {
  siteId: string
  siteName: string
  mfaEnabled: boolean
}) {
  const client = useQueryClient()
  const [phase, setPhase] = useState<WizardPhase>('scope')
  const [categories] = useState<DataResetCategory[]>(RESET_CATEGORIES.map((item) => item.id))
  const [deleteBillDocuments, setDeleteBillDocuments] = useState(false)
  const [disconnectedPolicy, setDisconnectedPolicy] = useState<DisconnectedSensorPolicy>('defer_until_reconnect')
  const [backupMode, setBackupMode] = useState<DataResetBackupMode>('verified_backup')
  const [noBackupAcknowledged, setNoBackupAcknowledged] = useState(false)
  const [confirmation, setConfirmation] = useState('')
  const [reason, setReason] = useState('')
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID())
  const [resumeRecord, setResumeRecord] = useState(() => readResumeRecord(siteId))
  const [operationId, setOperationId] = useState(resumeRecord?.operationId)
  const [protectedAction, setProtectedAction] = useState<ProtectedAction>()
  const [actionReason, setActionReason] = useState('')
  const [actionIdempotencyKey, setActionIdempotencyKey] = useState(() => crypto.randomUUID())
  const [clock, setClock] = useState(() => Date.now())

  const planRequest = useMutation({
    mutationFn: () => request('/api/v1/system/data-reset/plan', json('POST', {
      site_id: siteId,
      categories,
      delete_imported_bill_documents: deleteBillDocuments,
      disconnected_sensor_policy: disconnectedPolicy,
    }), adaptDataResetPlan),
    onSuccess: () => {
      setIdempotencyKey(crypto.randomUUID())
      setConfirmation('')
      setPhase('plan')
    },
    onError: (error) => {
      if (!(error instanceof ApiError) || error.problem.code !== 'data_reset_active' || !error.problem.operation_id) return
      const active: DataResetResumeRecord = {
        operationId: error.problem.operation_id,
        participantNames: {},
        planCounts: {},
        categories: [],
        preserved: [],
      }
      localStorage.setItem(operationStorageKey(siteId), JSON.stringify(active))
      setResumeRecord(active)
      setOperationId(active.operationId)
    },
  })
  const plan = planRequest.data

  const operationQuery = useQuery({
    queryKey: ['data-reset-operation', operationId],
    queryFn: () => request(`/api/v1/system/data-reset/${encodeURIComponent(operationId ?? '')}`, {}, adaptDataResetOperation),
    enabled: Boolean(operationId),
    retry: false,
    refetchInterval: (query) => shouldPoll(query.state.data?.state),
  })

  const execute = useMutation({
    mutationFn: () => {
      if (!plan) throw new Error('Generate and review a dry-run plan first.')
      return request('/api/v1/system/data-reset/execute', json('POST', {
        plan_id: plan.planId,
        plan_revision: plan.revision,
        idempotency_key: idempotencyKey,
        reason: reason.trim(),
        backup_mode: backupMode,
        confirmation_phrase: confirmation,
        permanent_without_backup_acknowledged: backupMode === 'permanent_without_backup' && noBackupAcknowledged,
      }), adaptDataResetOperation)
    },
    onSuccess: (operation) => {
      if (!plan) return
      const stored = writeResumeRecord(siteId, plan, operation.operationId)
      setResumeRecord(stored)
      setOperationId(operation.operationId)
      client.setQueryData(['data-reset-operation', operation.operationId], operation)
    },
    onError: (error) => {
      if (isProblem(error, 'reauthentication_required')) setProtectedAction('execute')
    },
  })

  const retry = useMutation({
    mutationFn: () => request(`/api/v1/system/data-reset/${encodeURIComponent(operationId ?? '')}/retry`, json('POST', {
      idempotency_key: actionIdempotencyKey,
      reason: actionReason.trim(),
    }), adaptDataResetOperation),
    onSuccess: (operation) => {
      client.setQueryData(['data-reset-operation', operation.operationId], operation)
      setActionReason('')
      setActionIdempotencyKey(crypto.randomUUID())
    },
    onError: (error) => {
      if (isProblem(error, 'reauthentication_required')) setProtectedAction('retry')
    },
  })

  const cancel = useMutation({
    mutationFn: () => request(`/api/v1/system/data-reset/${encodeURIComponent(operationId ?? '')}/cancel`, json('POST', {
      idempotency_key: actionIdempotencyKey,
      reason: actionReason.trim(),
    }), adaptDataResetOperation),
    onSuccess: (operation) => {
      client.setQueryData(['data-reset-operation', operation.operationId], operation)
      setActionReason('')
      setActionIdempotencyKey(crypto.randomUUID())
    },
    onError: (error) => {
      if (isProblem(error, 'reauthentication_required')) setProtectedAction('cancel')
    },
  })

  const operation = operationQuery.data
  const busy = execute.isPending || retry.isPending || cancel.isPending
  const exactPhrase = backupMode === 'verified_backup'
    ? plan?.confirmationPhrases?.verifiedBackup
    : plan?.confirmationPhrases?.permanentWithoutBackup
  const planExpired = plan ? Date.parse(plan.expiresAt) <= clock : false
  const executeReady = Boolean(
    exactPhrase
    && confirmation === exactPhrase
    && reason.trim().length >= 8
    && !planExpired
    && (backupMode === 'verified_backup' || noBackupAcknowledged),
  )

  useEffect(() => {
    const timer = window.setInterval(() => { setClock(Date.now()) }, 1_000)
    return () => { window.clearInterval(timer) }
  }, [])

  function clearSavedOperation() {
    localStorage.removeItem(operationStorageKey(siteId))
    setOperationId(undefined)
    setResumeRecord(undefined)
    setPhase('scope')
    setActionReason('')
    planRequest.reset()
    execute.reset()
    retry.reset()
    cancel.reset()
  }

  function runProtectedAction() {
    const action = protectedAction
    setProtectedAction(undefined)
    if (action === 'execute') execute.mutate()
    if (action === 'retry') retry.mutate()
    if (action === 'cancel') cancel.mutate()
  }

  if (operationId) {
    return (
      <>
        <DataResetOperationView
          operation={operation}
          loading={operationQuery.isLoading}
          error={operationQuery.error}
          participantNames={resumeRecord?.participantNames ?? {}}
          actionReason={actionReason}
          setActionReason={setActionReason}
          actionIdempotencyKey={actionIdempotencyKey}
          busy={busy}
          retryError={retry.error}
          cancelError={cancel.error}
          refresh={() => void operationQuery.refetch()}
          retryOperation={() => { setProtectedAction('retry') }}
          cancelOperation={() => { setProtectedAction('cancel') }}
          clearSavedOperation={clearSavedOperation}
        />
        {protectedAction && <ProtectedChangeDialog
          mfaEnabled={mfaEnabled}
          eyebrow="Data reset authorization"
          title={protectedAction === 'cancel' ? 'Authorize reset cancellation' : 'Authorize reset recovery'}
          description={mfaEnabled ? 'Enter your current password or MFA code. The server requires recent reauthentication and records this protected action in the audit log.' : 'Enter your current password. The server requires recent reauthentication and records this protected action in the audit log.'}
          submitLabel={protectedAction === 'cancel' ? 'Authorize cancellation' : 'Authorize retry'}
          onCancel={() => { setProtectedAction(undefined) }}
          onConfirmed={runProtectedAction}
        />}
      </>
    )
  }

  return (
    <div className="data-reset-workflow">
      <Surface
        className="data-reset-intro"
        title="Reset readings and pricing history"
        subtitle="A coordinated data-only factory reset for this home and its assigned sensors."
      >
        <p>This permanently removes measurement, energy, cost, tier, and selected pricing history from the server and participating sensors.</p>
        <InlineNotice tone="warning"><strong>Network, security, enrollment, users, and hardware settings will be preserved.</strong> Wi-Fi, certificates, device credentials, administrator passwords, PZEM and CT configuration, and the current effective pricing configuration are not reset.</InlineNotice>
        <p className="muted-copy">This is separate from a credential-erasing factory reset. Sensor microSD reading files are cleared without formatting the card, and the PZEM hardware energy counter is not reset.</p>
      </Surface>

      <WizardSteps phase={phase} />

      {phase === 'scope' && <ScopeStep
        siteId={siteId}
        siteName={siteName}
        categories={categories}
        deleteBillDocuments={deleteBillDocuments}
        setDeleteBillDocuments={setDeleteBillDocuments}
        disconnectedPolicy={disconnectedPolicy}
        setDisconnectedPolicy={setDisconnectedPolicy}
        loading={planRequest.isPending}
        error={planRequest.error}
        createPlan={() => { planRequest.mutate() }}
      />}

      {phase === 'plan' && plan && <PlanStep
        plan={plan}
        regenerate={() => { setPhase('scope'); planRequest.reset() }}
        continueToBackup={() => { setPhase('backup') }}
      />}

      {phase === 'backup' && plan && <BackupStep
        plan={plan}
        backupMode={backupMode}
        setBackupMode={(mode) => { setBackupMode(mode); setConfirmation(''); setNoBackupAcknowledged(false) }}
        noBackupAcknowledged={noBackupAcknowledged}
        setNoBackupAcknowledged={setNoBackupAcknowledged}
        back={() => { setPhase('plan') }}
        continueToConfirm={() => { setPhase('confirm') }}
      />}

      {phase === 'confirm' && plan && <ConfirmationStep
        plan={plan}
        backupMode={backupMode}
        exactPhrase={exactPhrase}
        confirmation={confirmation}
        setConfirmation={setConfirmation}
        reason={reason}
        setReason={setReason}
        idempotencyKey={idempotencyKey}
        planExpired={planExpired}
        ready={executeReady}
        busy={execute.isPending}
        error={execute.error}
        back={() => { setPhase('backup') }}
        authorize={() => { setProtectedAction('execute') }}
      />}

      {protectedAction === 'execute' && <ProtectedChangeDialog
        mfaEnabled={mfaEnabled}
        eyebrow="Data reset authorization"
        title="Authorize permanent data deletion"
        description={mfaEnabled ? 'Enter your current password or MFA code. The exact reset phrase and deletion scope will be checked again by the server after this recent reauthentication.' : 'Enter your current password. The exact reset phrase and deletion scope will be checked again by the server after this recent reauthentication.'}
        submitLabel="Authorize data reset"
        onCancel={() => { setProtectedAction(undefined) }}
        onConfirmed={runProtectedAction}
      />}
    </div>
  )
}

function WizardSteps({ phase }: { phase: WizardPhase }) {
  const current = ['scope', 'plan', 'backup', 'confirm'].indexOf(phase)
  return <ol className="data-reset-steps" aria-label="Data reset steps">
    {['Scope', 'Dry-run plan', 'Backup', 'Review & authorize'].map((label, index) => <li className={index === current ? 'active' : index < current ? 'complete' : ''} key={label}>{index < current ? <CheckCircle2 /> : <span>{index + 1}</span>} {label}</li>)}
  </ol>
}

function ScopeStep({
  siteId,
  siteName,
  categories,
  deleteBillDocuments,
  setDeleteBillDocuments,
  disconnectedPolicy,
  setDisconnectedPolicy,
  loading,
  error,
  createPlan,
}: {
  siteId: string
  siteName: string
  categories: DataResetCategory[]
  deleteBillDocuments: boolean
  setDeleteBillDocuments: (value: boolean) => void
  disconnectedPolicy: DisconnectedSensorPolicy
  setDisconnectedPolicy: (value: DisconnectedSensorPolicy) => void
  loading: boolean
  error: unknown
  createPlan: () => void
}) {
  return <Surface title="1. Choose scope and data categories" subtitle="The dry-run plan is read-only. Nothing is deleted while counts and sensor readiness are calculated.">
    <div className="data-reset-scope-summary">
      <HomeScopeLabel siteName={siteName} siteId={siteId} />
      <span><Radio /><strong>All active sensors assigned to this home</strong><small>Other homes and sensors are never selected.</small></span>
    </div>
    <fieldset className="data-reset-options">
      <legend>Data to reset</legend>
      {RESET_CATEGORIES.map((category) => <label key={category.id}>
        <input
          type="checkbox"
          checked={categories.includes(category.id)}
          disabled
          onChange={() => {}}
        />
        <span><strong>{category.label} · Required</strong><small>{category.description}</small></span>
      </label>)}
    </fieldset>
    <label className="data-reset-sensitive-option">
      <input
        type="checkbox"
        checked={deleteBillDocuments}
        onChange={(event) => { setDeleteBillDocuments(event.target.checked) }}
      />
      <span><strong>Also permanently delete imported utility-bill documents and source artifacts</strong><small>Off by default. Pricing-history deletion alone preserves uploaded bills, OCR artifacts, and source evidence.</small></span>
    </label>
    {deleteBillDocuments && <InlineNotice tone="danger"><strong>Privacy deletion selected.</strong> Imported bill documents and their source artifacts may contain personally identifying account information and cannot be recovered by this operation unless the selected backup is usable.</InlineNotice>}
    <fieldset className="data-reset-options compact-options">
      <legend>Disconnected sensor policy</legend>
      <label><input type="radio" name="disconnected-policy" checked={disconnectedPolicy === 'defer_until_reconnect'} onChange={() => { setDisconnectedPolicy('defer_until_reconnect') }} /><span><strong>Reset on authenticated reconnect</strong><small>Old-generation uploads remain blocked. The result stays pending until each disconnected active sensor clears its local history.</small></span></label>
      <label><input type="radio" name="disconnected-policy" checked={disconnectedPolicy === 'block'} onChange={() => { setDisconnectedPolicy('block') }} /><span><strong>Block until every sensor is ready</strong><small>Execution cannot start while an active sensor is disconnected or unsupported.</small></span></label>
    </fieldset>
    {Boolean(error) && <DataResetError error={error} />}
    <div className="form-actions"><button className="button primary" type="button" disabled={loading || !categories.includes('measurement_history')} onClick={createPlan}>{loading ? 'Calculating exact counts…' : 'Create read-only dry-run plan'}</button></div>
  </Surface>
}

function HomeScopeLabel({ siteName, siteId }: { siteName: string; siteId: string }) {
  return <span><HardDrive /><strong>{siteName}</strong><small>Current home · {siteId}</small></span>
}

function PlanStep({
  plan,
  regenerate,
  continueToBackup,
}: {
  plan: DataResetPlan
  regenerate: () => void
  continueToBackup: () => void
}) {
  const connected = plan.participants.filter((item) => item.classification === 'connected' && item.supported).length
  const pending = plan.participants.filter((item) => item.classification === 'disconnected').length
  return <>
    <Surface title="2. Review the dry-run plan" subtitle={`Plan revision ${plan.revision} · expires ${relativeTime(plan.expiresAt)} · no deletion has occurred.`}>
      <PlanCounts plan={plan} />
      {connected > 0 && <InlineNotice tone="warning"><strong>Connected-sensor measurement pause.</strong> After authorization, participating sensors temporarily pause new measurement recording while prepare, backup verification, commit, or cancellation completes. Existing readings are not deleted during prepare, and the firmware records durable pause evidence.</InlineNotice>}
      <details className="data-reset-exact-counts"><summary>Exact server row counts by table</summary><dl>{Object.entries(plan.counts).sort(([left], [right]) => left.localeCompare(right)).map(([key, value]) => <div key={key}><dt>{statusText(key)}</dt><dd>{value.toLocaleString()}</dd></div>)}</dl></details>
      <div className="data-reset-summary-line"><span><strong>{connected}</strong><small>Sensors resetting now</small></span><span><strong>{pending}</strong><small>Pending authenticated reconnect</small></span><span><strong>{plan.resetGeneration}</strong><small>New data generation</small></span></div>
    </Surface>
    <Surface title="Sensor participation" subtitle="Sequence boundaries are server-authoritative and preserve monotonic identity.">
      <div className="data-reset-participants">{plan.participants.length ? plan.participants.map((participant) => <article key={participant.deviceId}>
        <header><span><Radio /><strong>{participant.name}</strong></span><span className={`pill ${participant.classification === 'connected' ? 'success' : participant.classification === 'disconnected' ? 'warning' : 'danger'}`}>{participant.classification === 'connected' ? 'Will reset now' : participant.classification === 'authentication_failed' ? 'Authentication failed' : participant.classification === 'disconnected' ? 'Pending reset on reconnect' : participant.classification === 'unsupported' ? 'Unsupported firmware' : 'Excluded'}</span></header>
        <dl>
          <div><dt>Device UUID</dt><dd>{participant.deviceId}</dd></div>
          <div><dt>Connection</dt><dd>{statusText(participant.classification)}</dd></div>
          <div><dt>Firmware</dt><dd>{participant.firmwareVersion ?? 'Unavailable'}</dd></div>
          <div><dt>Reset capability</dt><dd>{participant.supported ? 'data-reset/1.0.0' : 'Unsupported'}</dd></div>
          <div><dt>Local records</dt><dd>{participant.recordCountStatus === 'unavailable' ? 'Unavailable' : participant.recordCountStatus === 'not_applicable' ? 'Not applicable' : participant.localRecordCount?.toLocaleString() ?? 'Unavailable'}{participant.recordCountStatus === 'exact_prepare_projection' ? ' (exact)' : participant.recordCountStatus === 'last_reported' ? ' (last reported)' : ''}</dd></div>
          <div><dt>Backlog</dt><dd>{participant.backlog?.toLocaleString() ?? 'Unavailable'}</dd></div>
          <div><dt>Sequence boundary</dt><dd>{participant.boundary.toLocaleString()}</dd></div>
          <div><dt>Card generation</dt><dd>{participant.cardGeneration ?? 'Unavailable'}</dd></div>
          <div><dt>Prepare / commit / verify</dt><dd>Not started / Not started / Not started</dd></div>
        </dl>
      </article>) : <InlineNotice>No assigned sensors are included. Central history can still be reset.</InlineNotice>}</div>
    </Surface>
    <Surface title="Current pricing will be preserved" subtitle="The server compares nonsecret configuration digests before and after creating the reset baseline.">
      {plan.pricing.length ? plan.pricing.map((pricing) => <div className="list-row" key={pricing.utilityAccountId}><ShieldCheck /><span><strong>{pricing.ratePlanName ?? 'Current effective rate plan'}</strong><small>{pricing.utilityAccountName ?? pricing.utilityAccountId} · version {pricing.rateVersionId}</small></span><span className="pill success">Digest recorded</span></div>) : <InlineNotice>No active pricing assignment exists; the reset will not invent one.</InlineNotice>}
      <PreservedList values={plan.preserved} />
    </Surface>
    <div className="data-reset-wizard-actions"><button type="button" className="button secondary" onClick={regenerate}>Change scope and regenerate</button><button type="button" className="button primary" onClick={continueToBackup}>Continue to backup choice</button></div>
  </>
}

function PlanCounts({ plan }: { plan: DataResetPlan }) {
  const rollups = count(plan.counts, 'daily_device_rollups', 'monthly_device_rollups', 'site_rollups', 'rollups')
  const costs = count(plan.counts, 'cost_rows', 'cost_interval_results', 'daily_cost_rollups', 'cost_calculation_runs')
  const tiers = count(plan.counts, 'tier_rows', 'tier_allocation_segments', 'cycle_tier_summaries', 'tier_projection_snapshots')
  const pricing = count(plan.counts, 'historical_pricing_rows')
  const sensorRecords = plan.sensorRecordsToDeleteNow
  const entries = [
    ['Server readings', plan.counts.raw_readings ?? 0],
    ['Normalized intervals', plan.counts.normalized_intervals ?? 0],
    ['Power and energy rollups', rollups],
    ['Sensor records to delete now', sensorRecords],
    ['Cost rows', costs],
    ['Tier rows', tiers],
    ['Historical pricing records', pricing],
    ['Exports', plan.counts.exports ?? 0],
    ['Reports', plan.counts.reports ?? 0],
  ] as const
  return <div className="data-reset-count-grid">{entries.map(([label, value]) => <div key={label}><small>{label}</small><strong>{value.toLocaleString()}</strong></div>)}</div>
}

function PreservedList({ values }: { values: string[] }) {
  return <div className="data-reset-preserved"><h3>Preserved configuration</h3><ul>{values.map((value) => <li key={value}><CheckCircle2 /> {PRESERVED_LABELS[value] ?? statusText(value)}</li>)}</ul></div>
}

function BackupStep({
  plan,
  backupMode,
  setBackupMode,
  noBackupAcknowledged,
  setNoBackupAcknowledged,
  back,
  continueToConfirm,
}: {
  plan: DataResetPlan
  backupMode: DataResetBackupMode
  setBackupMode: (value: DataResetBackupMode) => void
  noBackupAcknowledged: boolean
  setNoBackupAcknowledged: (value: boolean) => void
  back: () => void
  continueToConfirm: () => void
}) {
  return <Surface title="3. Choose backup protection" subtitle="A backup is created and verified by default before the central commit boundary.">
    <fieldset className="data-reset-backup-options">
      <legend>Backup mode</legend>
      <label className={backupMode === 'verified_backup' ? 'selected' : ''}><input type="radio" name="backup-mode" checked={backupMode === 'verified_backup'} onChange={() => { setBackupMode('verified_backup') }} /><DatabaseBackup /><span><strong>Create and verify backup before reset</strong><small>Deletion stops before central commit if backup creation or verification fails.</small></span></label>
      <label className={backupMode === 'permanent_without_backup' ? 'selected danger-choice' : ''}><input type="radio" name="backup-mode" checked={backupMode === 'permanent_without_backup'} onChange={() => { setBackupMode('permanent_without_backup') }} /><Trash2 /><span><strong>Permanently reset without backup</strong><small>This deliberate path is irreversible and uses a stronger server-provided confirmation phrase.</small></span></label>
    </fieldset>
    {backupMode === 'permanent_without_backup' && <>
      <InlineNotice tone="danger"><strong>No recovery backup will be created.</strong> The server, sensor, pricing, and generated history selected in plan {plan.planId} cannot be restored by this operation.</InlineNotice>
      <label className="data-reset-sensitive-option"><input type="checkbox" checked={noBackupAcknowledged} onChange={(event) => { setNoBackupAcknowledged(event.target.checked) }} /><span><strong>I understand this reset is permanent and has no reset backup</strong><small>This acknowledgement is separate from the stronger typed phrase required on the next step.</small></span></label>
    </>}
    <div className="data-reset-wizard-actions"><button type="button" className="button secondary" onClick={back}>Back to plan</button><button type="button" className="button primary" disabled={backupMode === 'permanent_without_backup' && !noBackupAcknowledged} onClick={continueToConfirm}>Review and authorize</button></div>
  </Surface>
}

function ConfirmationStep({
  plan,
  backupMode,
  exactPhrase,
  confirmation,
  setConfirmation,
  reason,
  setReason,
  idempotencyKey,
  planExpired,
  ready,
  busy,
  error,
  back,
  authorize,
}: {
  plan: DataResetPlan
  backupMode: DataResetBackupMode
  exactPhrase?: string
  confirmation: string
  setConfirmation: (value: string) => void
  reason: string
  setReason: (value: string) => void
  idempotencyKey: string
  planExpired: boolean
  ready: boolean
  busy: boolean
  error: unknown
  back: () => void
  authorize: () => void
}) {
  return <>
    <Surface title="4. Final review" subtitle={`Plan ${plan.planId} · revision ${plan.revision} · reset generation ${plan.resetGeneration}`}>
      <PlanCounts plan={plan} />
      <dl className="data-reset-review-list">
        <div><dt>Home</dt><dd>{plan.site.name} ({plan.site.id})</dd></div>
        <div><dt>Sensors resetting now</dt><dd>{plan.participants.filter((item) => item.classification === 'connected' && item.supported).length}</dd></div>
        <div><dt>Sensors pending reconnect</dt><dd>{plan.participants.filter((item) => item.classification === 'disconnected').length}</dd></div>
        <div><dt>Imported bill documents</dt><dd>{plan.deleteImportedBillDocuments ? `Delete ${plan.counts.imported_bill_documents ?? 0}` : 'Preserve'}</dd></div>
        <div><dt>Backup</dt><dd>{backupMode === 'verified_backup' ? 'Create and verify before deletion' : 'Permanent reset without backup'}</dd></div>
        <div><dt>Plan expires</dt><dd>{dateTime(plan.expiresAt)}</dd></div>
      </dl>
      <PreservedList values={plan.preserved} />
    </Surface>
    <Surface title="Authentication and exact confirmation" subtitle="The server requires an administrator, system.data_reset, CSRF protection, an unexpired plan, and recent password or MFA verification.">
      {planExpired && <InlineNotice tone="danger"><strong>This plan has expired.</strong> Return to scope and generate new exact counts.</InlineNotice>}
      {!exactPhrase && <InlineNotice tone="danger"><strong>Execution disabled.</strong> The server did not return an exact confirmation phrase for this backup mode. Regenerate the plan after the API contract is repaired.</InlineNotice>}
      {backupMode === 'permanent_without_backup' && <InlineNotice tone="danger">This no-backup execution is not reversible. Verify the stronger phrase character-for-character.</InlineNotice>}
      {exactPhrase && <div className="data-reset-phrase"><small>Type this exact server-provided phrase</small><code>{exactPhrase}</code></div>}
      <div className="form-grid single data-reset-confirm-form">
        <label>Exact confirmation phrase<input autoComplete="off" spellCheck={false} value={confirmation} onChange={(event) => { setConfirmation(event.target.value) }} /></label>
        <label>Audit reason<textarea required minLength={8} maxLength={500} rows={3} value={reason} onChange={(event) => { setReason(event.target.value) }} placeholder="Explain why the reset is required" /></label>
        <label>Idempotency key<input readOnly value={idempotencyKey} /></label>
      </div>
      {confirmation && exactPhrase && confirmation !== exactPhrase && <InlineNotice tone="warning">The phrase does not match exactly. Capitalization and spacing matter.</InlineNotice>}
      {Boolean(error) && <DataResetError error={error} />}
      <div className="data-reset-wizard-actions"><button type="button" className="button secondary" onClick={back}>Back to backup</button><button type="button" className="button danger" disabled={!ready || busy} onClick={authorize}><LockKeyhole /> {busy ? 'Starting protected reset…' : 'Verify identity and start reset'}</button></div>
    </Surface>
  </>
}

function DataResetOperationView({
  operation,
  loading,
  error,
  participantNames,
  actionReason,
  setActionReason,
  actionIdempotencyKey,
  busy,
  retryError,
  cancelError,
  refresh,
  retryOperation,
  cancelOperation,
  clearSavedOperation,
}: {
  operation?: DataResetOperation
  loading: boolean
  error: unknown
  participantNames: Record<string, string>
  actionReason: string
  setActionReason: (value: string) => void
  actionIdempotencyKey: string
  busy: boolean
  retryError: unknown
  cancelError: unknown
  refresh: () => void
  retryOperation: () => void
  cancelOperation: () => void
  clearSavedOperation: () => void
}) {
  if (loading) return <Surface title="Reset readings and pricing history" subtitle="Resuming the durable operation saved for this home."><LoadingState label="Loading reset checkpoints…" /></Surface>
  if (error || !operation) return <Surface title="Reset readings and pricing history" subtitle="The saved operation could not be resumed."><ErrorState error={error ?? new Error('The operation response was empty.')} retry={refresh} /><button type="button" className="button secondary" onClick={clearSavedOperation}>Forget saved operation</button></Surface>
  const cancellable = !operation.centralCommitAt && !['database_reset_committed', 'sensor_commit_running', 'verification_running', 'completed', 'completed_with_resets_pending_on_reconnect', 'partial_failure', 'attention_required', 'cancelled'].includes(operation.state)
  const retryable = ['partial_failure', 'attention_required'].includes(operation.state)
  const actionReady = actionReason.trim().length >= 8
  const pending = operation.participants.filter((item) => item.state === 'pending_reconnect' || item.state === 'unreachable')
  const verified = operation.participants.filter((item) => item.state === 'verified')
  const terminal = isTerminal(operation.state)
  return <div className="data-reset-operation">
    <Surface
      className={`data-reset-operation-header ${stateTone(operation.state)}`}
      title="Reset readings and pricing history"
      subtitle={`Operation ${operation.operationId} · revision ${operation.revision}`}
      action={<button type="button" className="button secondary compact" onClick={refresh}><RefreshCw /> Refresh</button>}
    >
      <div className="data-reset-operation-state"><span className={`pill ${stateTone(operation.state)}`}>{statusText(operation.state)}</span><strong>{statusText(operation.stage)}</strong><small>Started {dateTime(operation.startedAt)} · generation {operation.resetGeneration}</small></div>
      {operation.failureCode && <InlineNotice tone="danger"><strong>{operation.failureCode}: </strong>{operation.failureSummary ?? 'This checkpoint requires attention.'}</InlineNotice>}
      {operation.state === 'completed_with_resets_pending_on_reconnect' && <InlineNotice tone="warning"><strong>Central reset committed with sensors pending.</strong> Old-generation uploads remain blocked. This operation stays visible and retries authenticated reset before normal synchronization when each sensor reconnects.</InlineNotice>}
      {operation.state === 'failed_before_commit' && <InlineNotice tone="warning">Failure occurred before the central commit boundary; measurement and pricing history should remain intact. Create and review a new plan before trying again.</InlineNotice>}
      <div className="data-reset-summary-line"><span><strong>{verified.length}</strong><small>Sensors verified</small></span><span><strong>{pending.length}</strong><small>Pending reconnect</small></span><span><strong>{operation.centralCommitAt ? 'Passed' : 'Not passed'}</strong><small>Central commit boundary</small></span></div>
    </Surface>

    <Surface title="Server and sensor progress" subtitle="Only redacted checkpoint evidence is shown. Reading values, credentials, HMAC material, and raw sensor receipts are never returned to the browser.">
      <ol className="data-reset-progress">{PROGRESS_STEPS.map(([target, label], index) => {
        const progress = progressState(operation.state, target, index)
        return <li className={progress} key={`${target}-${label}`}>{progress === 'complete' ? <CheckCircle2 /> : progress === 'active' ? <Clock3 /> : progress === 'failed' ? <XCircle /> : <Circle />}<span><strong>{label}</strong><small>{progress === 'complete' ? 'Checkpoint completed' : progress === 'active' ? 'In progress' : progress === 'failed' ? 'Needs attention' : 'Waiting'}</small></span></li>
      })}</ol>
      <div className="data-reset-participants operation-participants">{operation.participants.map((participant) => <article key={participant.deviceId}>
        <header><span><Radio /><strong>{participant.name ?? participantNames[participant.deviceId] ?? participant.deviceId}</strong></span><span className={`pill ${participant.state === 'verified' ? 'success' : ['failed', 'attention_required'].includes(participant.state) ? 'danger' : ['pending_reconnect', 'unreachable', 'unsupported'].includes(participant.state) ? 'warning' : ''}`}>{statusText(participant.state)}</span></header>
        <dl>
          <div><dt>Device UUID</dt><dd>{participant.deviceId}</dd></div>
          <div><dt>Firmware</dt><dd>{participant.firmwareVersion ?? 'Unavailable'}</dd></div>
          <div><dt>Reset boundary</dt><dd>{participant.resetBoundary.toLocaleString()}</dd></div>
          <div><dt>New sequence floor</dt><dd>{participant.newSequenceFloor?.toLocaleString() ?? 'Not yet reported'}</dd></div>
          <div><dt>Next sequence</dt><dd>{participant.newNextSequence?.toLocaleString() ?? 'Not yet reported'}</dd></div>
          <div><dt>Prepare</dt><dd>{participant.preparedAt ? dateTime(participant.preparedAt) : participant.state === 'prepare_requested' ? 'Requested' : 'Waiting'}</dd></div>
          <div><dt>Commit</dt><dd>{participant.committedAt ? dateTime(participant.committedAt) : participant.state === 'commit_requested' ? 'Requested' : 'Waiting'}</dd></div>
          <div><dt>Verification</dt><dd>{participant.verifiedAt ? dateTime(participant.verifiedAt) : participant.failureCode ?? 'Waiting'}</dd></div>
        </dl>
        {participant.failureSummary && <InlineNotice tone="danger"><strong>{participant.failureCode}: </strong>{participant.failureSummary}</InlineNotice>}
      </article>)}</div>
    </Surface>

    {(retryable || cancellable) && <Surface title="Recovery controls" subtitle={operation.centralCommitAt ? 'The commit boundary has passed. Retry only missing idempotent checkpoints.' : 'Cancellation is allowed only while central deletion has not committed.'}>
      <label>Retry or cancellation reason<input minLength={8} maxLength={500} value={actionReason} onChange={(event) => { setActionReason(event.target.value) }} /></label>
      <label>Recovery idempotency key<input readOnly value={actionIdempotencyKey} /></label>
      <div className="inline-actions">
        {retryable && <button type="button" className="button primary" disabled={!actionReady || busy} onClick={retryOperation}><RotateCcw /> Verify identity and retry missing checkpoints</button>}
        {cancellable && <button type="button" className="button danger" disabled={!actionReady || busy} onClick={cancelOperation}>Verify identity and cancel before commit</button>}
      </div>
      {Boolean(retryError) && <DataResetError error={retryError} />}
      {Boolean(cancelError) && <DataResetError error={cancelError} />}
    </Surface>}

    {(operation.state === 'completed' || operation.state === 'completed_with_resets_pending_on_reconnect') && <DataResetResult operation={operation} />}
    {terminal && <div className="data-reset-wizard-actions"><button type="button" className="button secondary" onClick={clearSavedOperation}>{operation.state === 'completed' ? 'Close result and create a new plan' : 'Close operation'}</button></div>}
  </div>
}

function progressState(current: DataResetOperationState, target: DataResetOperationState, duplicateIndex: number): 'waiting' | 'active' | 'complete' | 'failed' {
  if (['partial_failure', 'attention_required', 'failed_before_commit'].includes(current)) return 'failed'
  if (current === 'cancelled') return 'waiting'
  if (current === 'completed') return 'complete'
  const currentIndex = OPERATION_STAGE_ORDER.indexOf(current)
  const targetIndex = OPERATION_STAGE_ORDER.indexOf(target)
  if (current === 'completed_with_resets_pending_on_reconnect' && target === 'completed') return 'waiting'
  if (currentIndex > targetIndex) return 'complete'
  if (currentIndex < targetIndex) return 'waiting'
  if (target === 'preparing_sensors' && duplicateIndex === 0) return 'active'
  return 'active'
}

function DataResetResult({ operation }: { operation: DataResetOperation }) {
  const evidenceCounts = numericRecord(operation.finalEvidence.deleted_counts)
  const countsReported = Object.keys(evidenceCounts).length > 0
  const pricingHashes = objectRecord(operation.finalEvidence.pricing_hashes)
  const configurationVerified = operation.participants.length === 0 || operation.participants.every((item) => item.state === 'verified' || item.state === 'pending_reconnect' || item.state === 'unreachable' || item.state === 'not_applicable')
  const verifiedSensors = operation.participants.filter((item) => item.state === 'verified')
  const pendingSensors = operation.participants.filter((item) => item.state === 'pending_reconnect' || item.state === 'unreachable')
  const removedCount = (...keys: string[]) => countsReported ? count(evidenceCounts, ...keys).toLocaleString() : 'Not reported'
  const resultRows = [
    ['Reset timestamp', dateTime(operation.resetTimestamp)],
    ['Reset generation', operation.resetGeneration.toLocaleString()],
    ['Readings removed', removedCount('raw_readings')],
    ['Normalized intervals removed', removedCount('normalized_intervals')],
    ['Cost history removed', removedCount('cost_rows', 'cost_interval_results', 'daily_cost_rollups', 'cost_calculation_runs')],
    ['Tier history removed', removedCount('tier_rows', 'tier_allocation_segments', 'cycle_tier_summaries', 'tier_projection_snapshots')],
    ['Historical pricing removed', removedCount('historical_pricing_rows')],
    ['Current pricing preserved', Object.keys(pricingHashes).length ? `Verified for ${Object.keys(pricingHashes).length} utility account(s)` : 'No active pricing digest reported'],
    ['Sensors reset', verifiedSensors.length.toLocaleString()],
    ['Sensors pending reconnect', pendingSensors.length.toLocaleString()],
    ['Backup reference', operation.backup.reference ?? operation.backup.backupId ?? (operation.backup.mode === 'permanent_without_backup' ? 'No backup selected' : 'Pending')],
    ['Backup checksum', operation.backup.manifestHash ?? (operation.backup.mode === 'permanent_without_backup' ? 'No backup selected' : 'Pending')],
    ['New billing baseline', removedCount('pricing_baselines')],
    ['New readings received', verificationStatus(operation.finalEvidence, 'new_readings_status', 'new_readings_received')],
    ['New cost calculation', verificationStatus(operation.finalEvidence, 'new_cost_status', 'new_cost_calculation_confirmed')],
  ] as const
  return <Surface title="Reset result" subtitle="Durable, redacted evidence remains available without retaining deleted readings.">
    <dl className="data-reset-result-list">{resultRows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <InlineNotice tone={configurationVerified ? 'success' : 'danger'}>{configurationVerified ? 'Configuration-preservation checkpoints passed for completed sensors. Pending sensors must verify on reconnect before the operation can become fully complete.' : 'configuration_preservation_verification_failed: one or more sensors changed or could not verify preserved configuration.'}</InlineNotice>
    <div className="data-reset-sequence-evidence"><h3>New monotonic sequence floors</h3>{operation.participants.map((participant) => <div key={participant.deviceId}><span>{participant.name ?? participant.deviceId}</span><strong>{participant.newSequenceFloor?.toLocaleString() ?? 'Not yet reported'} floor · {participant.newNextSequence?.toLocaleString() ?? 'Not yet reported'} next</strong></div>)}</div>
  </Surface>
}
