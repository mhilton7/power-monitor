import type {
  DataResetCategory,
  DataResetClassification,
  DataResetOperation,
  DataResetOperationState,
  DataResetParticipantState,
  DataResetPlan,
  DataResetPlanParticipant,
} from './types'

const CATEGORIES = new Set<DataResetCategory>([
  'measurement_history',
  'cost_history',
  'pricing_history',
  'generated_outputs',
])
const CLASSIFICATIONS = new Set<DataResetClassification>([
  'connected',
  'authentication_failed',
  'disconnected',
  'unsupported',
  'revoked',
  'removed',
])
const OPERATION_STATES = new Set<DataResetOperationState>([
  'planning',
  'awaiting_confirmation',
  'preparing_sensors',
  'sensors_prepared',
  'backup_running',
  'backup_verified',
  'database_reset_running',
  'database_reset_committed',
  'sensor_commit_running',
  'verification_running',
  'completed',
  'completed_with_resets_pending_on_reconnect',
  'partial_failure',
  'attention_required',
  'cancelled',
  'failed_before_commit',
])
const PARTICIPANT_STATES = new Set<DataResetParticipantState>([
  'pending',
  'unreachable',
  'unsupported',
  'prepare_requested',
  'prepared',
  'commit_requested',
  'committed',
  'verified',
  'pending_reconnect',
  'failed',
  'attention_required',
  'not_applicable',
])

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`The server returned an invalid ${label}.`)
  }
  return value as Record<string, unknown>
}

function requiredString(value: unknown, label: string): string {
  if (typeof value !== 'string' || !value) throw new Error(`The server omitted ${label}.`)
  return value
}

function optionalString(value: unknown): string | undefined {
  return typeof value === 'string' && value ? value : undefined
}

function nonnegativeNumber(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) {
    throw new Error(`The server returned an invalid ${label}.`)
  }
  return value
}

function optionalNonnegativeNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
    ? value
    : undefined
}

function booleanValue(value: unknown): boolean {
  return value === true
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function assertResetProtocol(source: Record<string, unknown>) {
  if (source.protocol !== undefined && source.protocol !== 'data-reset/1.0.0') {
    throw new Error('The server returned an unsupported data-reset protocol.')
  }
}

export function adaptDataResetPlan(value: unknown): DataResetPlan {
  const source = record(value, 'data-reset plan')
  assertResetProtocol(source)
  const site = record(source.site, 'data-reset site')
  const rawCategories = stringArray(source.categories)
  if (!rawCategories.length || rawCategories.some((item) => !CATEGORIES.has(item as DataResetCategory))) {
    throw new Error('The data-reset plan contains unsupported categories.')
  }
  const disconnectedPolicy = requiredString(source.disconnected_sensor_policy, 'the disconnected-sensor policy')
  if (disconnectedPolicy !== 'block' && disconnectedPolicy !== 'defer_until_reconnect') {
    throw new Error('The data-reset plan contains an unsupported disconnected-sensor policy.')
  }
  const countsSource = record(source.counts, 'data-reset counts')
  const counts = Object.fromEntries(Object.entries(countsSource).map(([key, count]) => [
    key,
    nonnegativeNumber(count, `count ${key}`),
  ]))
  const participants = Array.isArray(source.participants) ? source.participants.map((item) => {
    const participant = record(item, 'data-reset participant')
    const classification = requiredString(participant.classification, 'sensor classification')
    if (!CLASSIFICATIONS.has(classification as DataResetClassification)) {
      throw new Error('The data-reset plan contains an unsupported sensor classification.')
    }
    return {
      deviceId: requiredString(participant.device_id, 'sensor UUID'),
      name: requiredString(participant.name, 'sensor name'),
      classification: classification as DataResetClassification,
      supported: booleanValue(participant.supported),
      boundary: nonnegativeNumber(participant.boundary, 'sensor reset boundary'),
      estimatedSensorRecords: optionalNonnegativeNumber(participant.estimated_sensor_records),
      localRecordCount: optionalNonnegativeNumber(participant.local_record_count),
      backlog: optionalNonnegativeNumber(participant.backlog_estimate),
      recordCountStatus: (() => {
        const status = requiredString(participant.record_count_status, 'sensor record-count status')
        if (!['exact_prepare_projection', 'last_reported', 'unavailable', 'not_applicable'].includes(status)) {
          throw new Error('The data-reset plan contains an unsupported sensor record-count status.')
        }
        return status as DataResetPlanParticipant['recordCountStatus']
      })(),
      lastSeenAt: optionalString(participant.last_seen_at),
      firmwareVersion: optionalString(participant.firmware_version),
      firmwareBuildHash: optionalString(participant.firmware_build_hash),
      dataGeneration: nonnegativeNumber(participant.data_generation, 'sensor data generation'),
      serverHighestContiguous: nonnegativeNumber(participant.server_highest_contiguous, 'server sequence cursor'),
      serverMaximumSeen: nonnegativeNumber(participant.server_maximum_seen, 'maximum server sequence'),
      sensorAckSequence: nonnegativeNumber(participant.sensor_ack_sequence, 'sensor acknowledgement'),
      sensorNewestSequence: nonnegativeNumber(participant.sensor_newest_sequence, 'newest sensor sequence'),
      oldSequenceFloor: nonnegativeNumber(participant.old_sequence_floor, 'old sensor sequence floor'),
      oldNextSequence: nonnegativeNumber(participant.old_next_sequence, 'old next sequence'),
      cardGeneration: optionalString(participant.card_generation),
      cardIdentityStatus: optionalString(participant.card_identity_status),
      sdStatus: optionalString(participant.sd_status),
      probeStatus: optionalString(participant.probe_status),
    }
  }) : []
  const pricing = Array.isArray(source.pricing) ? source.pricing.map((item) => {
    const rate = record(item, 'pricing baseline')
    return {
      utilityAccountId: requiredString(rate.utility_account_id, 'utility account ID'),
      utilityAccountName: optionalString(rate.utility_account_name),
      ratePlanId: requiredString(rate.rate_plan_id, 'rate plan ID'),
      ratePlanName: optionalString(rate.rate_plan_name),
      rateVersionId: requiredString(rate.rate_version_id, 'rate version ID'),
      rateAssignmentId: optionalString(rate.rate_assignment_id),
      pricingConfigurationHash: requiredString(rate.pricing_configuration_hash, 'pricing preservation digest'),
    }
  }) : []
  const phrases = source.confirmation_phrases === undefined
    ? undefined
    : record(source.confirmation_phrases, 'confirmation phrases')
  return {
    planId: requiredString(source.plan_id, 'plan ID'),
    site: {
      id: requiredString(site.id, 'site ID'),
      name: requiredString(site.name, 'site name'),
      revision: optionalNonnegativeNumber(site.revision),
      timezone: optionalString(site.timezone),
    },
    categories: rawCategories as DataResetCategory[],
    deleteImportedBillDocuments: booleanValue(source.delete_imported_bill_documents),
    disconnectedSensorPolicy: disconnectedPolicy,
    resetTimestamp: requiredString(source.reset_timestamp, 'reset timestamp'),
    resetGeneration: nonnegativeNumber(source.reset_generation, 'reset generation'),
    counts,
    estimatedDatabaseBytes: optionalNonnegativeNumber(source.estimated_database_bytes),
    estimatedSensorRecords: optionalNonnegativeNumber(source.estimated_sensor_records),
    sensorRecordsToDeleteNow: nonnegativeNumber(source.sensor_records_to_delete_now, 'sensor records to delete now'),
    participants,
    pricing,
    preserved: stringArray(source.preserved),
    confirmationPhrases: phrases ? {
      verifiedBackup: requiredString(phrases.verified_backup, 'verified-backup confirmation phrase'),
      permanentWithoutBackup: requiredString(phrases.permanent_without_backup, 'no-backup confirmation phrase'),
    } : undefined,
    fingerprint: requiredString(source.fingerprint, 'plan fingerprint'),
    revision: nonnegativeNumber(source.revision, 'plan revision'),
    createdAt: requiredString(source.created_at, 'plan creation time'),
    expiresAt: requiredString(source.expires_at, 'plan expiration time'),
  }
}

function stageLabel(value: unknown, fallback: string): string {
  if (typeof value === 'string' && value) return value
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const source = value as Record<string, unknown>
    return optionalString(source.label) ?? optionalString(source.code) ?? optionalString(source.name) ?? fallback
  }
  return fallback
}

export function adaptDataResetOperation(value: unknown): DataResetOperation {
  const source = record(value, 'data-reset operation')
  assertResetProtocol(source)
  const state = requiredString(source.state, 'operation state')
  if (!OPERATION_STATES.has(state as DataResetOperationState)) {
    throw new Error('The server returned an unsupported data-reset operation state.')
  }
  const backup = record(source.backup, 'backup status')
  const backupMode = requiredString(backup.mode, 'backup mode')
  if (backupMode !== 'verified_backup' && backupMode !== 'permanent_without_backup') {
    throw new Error('The server returned an unsupported data-reset backup mode.')
  }
  const participants = Array.isArray(source.participants) ? source.participants.map((item) => {
    const participant = record(item, 'operation participant')
    const participantState = requiredString(participant.state, 'participant state')
    if (!PARTICIPANT_STATES.has(participantState as DataResetParticipantState)) {
      throw new Error('The server returned an unsupported participant state.')
    }
    return {
      deviceId: requiredString(participant.device_id, 'participant sensor UUID'),
      name: optionalString(participant.name),
      state: participantState as DataResetParticipantState,
      resetGeneration: nonnegativeNumber(participant.reset_generation, 'participant reset generation'),
      resetBoundary: nonnegativeNumber(participant.reset_boundary, 'participant reset boundary'),
      newSequenceFloor: optionalNonnegativeNumber(participant.new_sequence_floor),
      newNextSequence: optionalNonnegativeNumber(participant.new_next_sequence),
      firmwareVersion: optionalString(participant.firmware_version),
      failureCode: optionalString(participant.failure_code),
      failureSummary: optionalString(participant.failure_summary),
      lastAttemptAt: optionalString(participant.last_attempt_at),
      preparedAt: optionalString(participant.prepared_at),
      committedAt: optionalString(participant.committed_at),
      verifiedAt: optionalString(participant.verified_at),
    }
  }) : []
  const evidence = source.final_evidence === undefined
    ? {}
    : record(source.final_evidence, 'final verification evidence')
  return {
    operationId: requiredString(source.operation_id, 'operation ID'),
    planId: requiredString(source.plan_id, 'operation plan ID'),
    siteId: requiredString(source.site_id, 'operation site ID'),
    state: state as DataResetOperationState,
    stage: stageLabel(source.stage, state),
    revision: nonnegativeNumber(source.revision, 'operation revision'),
    resetGeneration: nonnegativeNumber(source.reset_generation, 'operation reset generation'),
    resetTimestamp: requiredString(source.reset_timestamp, 'operation reset timestamp'),
    backup: {
      mode: backupMode,
      backupId: optionalString(backup.backup_id),
      reference: optionalString(backup.reference),
      manifestHash: optionalString(backup.manifest_hash),
      verifiedAt: optionalString(backup.verified_at),
      recoverable: booleanValue(backup.recoverable),
    },
    recoverability: typeof source.recoverability === 'string'
      ? source.recoverability
      : stageLabel(source.recoverability, 'unknown'),
    participants,
    startedAt: requiredString(source.started_at, 'operation start time'),
    centralCommitAt: optionalString(source.central_commit_at),
    completedAt: optionalString(source.completed_at),
    failureCode: optionalString(source.failure_code),
    failureSummary: optionalString(source.failure_summary),
    finalEvidence: evidence,
  }
}
