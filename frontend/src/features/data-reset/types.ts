export type DataResetCategory =
  | 'measurement_history'
  | 'cost_history'
  | 'pricing_history'
  | 'generated_outputs'

export type DisconnectedSensorPolicy = 'block' | 'defer_until_reconnect'
export type DataResetBackupMode = 'verified_backup' | 'permanent_without_backup'

export type DataResetClassification =
  | 'connected'
  | 'authentication_failed'
  | 'disconnected'
  | 'unsupported'
  | 'revoked'
  | 'removed'

export type DataResetOperationState =
  | 'planning'
  | 'awaiting_confirmation'
  | 'preparing_sensors'
  | 'sensors_prepared'
  | 'backup_running'
  | 'backup_verified'
  | 'database_reset_running'
  | 'database_reset_committed'
  | 'sensor_commit_running'
  | 'verification_running'
  | 'completed'
  | 'completed_with_resets_pending_on_reconnect'
  | 'partial_failure'
  | 'attention_required'
  | 'cancelled'
  | 'failed_before_commit'

export type DataResetParticipantState =
  | 'pending'
  | 'unreachable'
  | 'unsupported'
  | 'prepare_requested'
  | 'prepared'
  | 'commit_requested'
  | 'committed'
  | 'verified'
  | 'pending_reconnect'
  | 'failed'
  | 'attention_required'
  | 'not_applicable'

export interface DataResetPlanParticipant {
  deviceId: string
  name: string
  classification: DataResetClassification
  supported: boolean
  boundary: number
  estimatedSensorRecords?: number
  localRecordCount?: number
  backlog?: number
  recordCountStatus: 'exact_prepare_projection' | 'last_reported' | 'unavailable' | 'not_applicable'
  lastSeenAt?: string
  firmwareVersion?: string
  firmwareBuildHash?: string
  dataGeneration: number
  serverHighestContiguous: number
  serverMaximumSeen: number
  sensorAckSequence: number
  sensorNewestSequence: number
  oldSequenceFloor: number
  oldNextSequence: number
  cardGeneration?: string
  cardIdentityStatus?: string
  sdStatus?: string
  probeStatus?: string
}

export interface DataResetPricingPlan {
  utilityAccountId: string
  utilityAccountName?: string
  ratePlanId: string
  ratePlanName?: string
  rateVersionId: string
  rateAssignmentId?: string
  pricingConfigurationHash: string
}

export interface DataResetPlan {
  planId: string
  site: { id: string; name: string; revision?: number; timezone?: string }
  categories: DataResetCategory[]
  deleteImportedBillDocuments: boolean
  disconnectedSensorPolicy: DisconnectedSensorPolicy
  resetTimestamp: string
  resetGeneration: number
  counts: Record<string, number>
  estimatedDatabaseBytes?: number
  estimatedSensorRecords?: number
  sensorRecordsToDeleteNow: number
  participants: DataResetPlanParticipant[]
  pricing: DataResetPricingPlan[]
  preserved: string[]
  confirmationPhrases?: {
    verifiedBackup: string
    permanentWithoutBackup: string
  }
  fingerprint: string
  revision: number
  createdAt: string
  expiresAt: string
}

export interface DataResetOperationParticipant {
  deviceId: string
  name?: string
  state: DataResetParticipantState
  resetGeneration: number
  resetBoundary: number
  newSequenceFloor?: number
  newNextSequence?: number
  firmwareVersion?: string
  failureCode?: string
  failureSummary?: string
  lastAttemptAt?: string
  preparedAt?: string
  committedAt?: string
  verifiedAt?: string
}

export interface DataResetOperation {
  operationId: string
  planId: string
  siteId: string
  state: DataResetOperationState
  stage: string
  revision: number
  resetGeneration: number
  resetTimestamp: string
  backup: {
    mode: DataResetBackupMode
    backupId?: string
    reference?: string
    manifestHash?: string
    verifiedAt?: string
    recoverable: boolean
  }
  recoverability: string
  participants: DataResetOperationParticipant[]
  startedAt: string
  centralCommitAt?: string
  completedAt?: string
  failureCode?: string
  failureSummary?: string
  finalEvidence: Record<string, unknown>
}

export interface DataResetResumeRecord {
  operationId: string
  participantNames: Record<string, string>
  planCounts: Record<string, number>
  categories: DataResetCategory[]
  preserved: string[]
}
