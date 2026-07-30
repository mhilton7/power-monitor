export interface ApiProblem {
  title: string
  detail: string
  status: number
  code: string
  errors?: Array<{ location: Array<string | number>; message: string }>
}

export interface UserSession {
  authenticated: boolean
  bootstrapRequired: boolean
  expiresAt?: string
  user?: {
    id: string
    email: string
    name: string
    roles: string[]
    permissions: string[]
    allHomes: boolean
    homeIds: string[]
  }
}

export interface Home {
  id: string
  name: string
  timezone: string
  currency: string
  lifecycle: 'active' | 'disabled' | 'removed'
  isDefault: boolean
  revision: number
  provider?: string
  billingDay?: number
}

export type HomeResolution =
  | { state: 'ready'; home: Home }
  | { state: 'missing' }
  | { state: 'multiple'; homes: Home[] }

export interface HomeSummary {
  currentPowerW?: string
  energyTodayKwh: string
  estimatedCostToday: string
  cycleEnergyKwh: string
  cycleEstimatedCost: string
  projectedBill?: string
  cycleDaysRemaining?: number
  cycleConfidence?: string
  onlineSensors: number
  reportingSensors: number
  totalSensors: number
  attentionSensors: number
  activeAlerts: number
  recentPeakW?: string
  latestDataAt?: string
  latestReceivedAt?: string
  serverNow?: string
  coveragePercent?: string
  hasLiveData: boolean
  hasEnergyData: boolean
  hasCostData: boolean
  currentPlan?: string
  currentRate?: string
  currentPeriod?: string
  nextPeriod?: string
  nextRate?: string
  nextPeriodAt?: string
  currentTier?: string
  remainingTierKwh?: string
  tierProgressPercent?: string
  pricingModel?: 'flat' | 'time_of_use' | 'tiered' | 'time_of_use_tiered'
  disclosure: string
}

export interface SensorSummary {
    id: string
    name: string
    homeId: string
    circuitId?: string
    utilityAccountId?: string
    state: string
  deviceStatus: string
  online: boolean
  currentPowerW?: string
  voltageVolts?: string
  currentAmps?: string
  frequencyHz?: string
  powerFactor?: string
  latestMeasurementAt?: string
  measurementReceivedAt?: string
  measurementSequence?: number
  measurementSource?: 'heartbeat_live' | 'committed_reading'
  measurementFreshness:
    | 'live'
    | 'waiting'
    | 'stale'
    | 'offline'
    | 'unavailable'
    | 'invalid'
    | 'needs_attention'
  invalidMetrics: string[]
  lastSeenAt?: string
  storageHealthy?: boolean
  wifiDbm?: number
    firmware?: string
    monitoredCircuit: string
    includedInDefault: boolean
    backlog: number
  ctRatingAmps: string
    measurementRole: string
}

export interface CircuitSummary {
  id: string
  homeId: string
  parentId?: string
  name: string
  measurementRole: 'main' | 'service-leg' | 'branch' | 'submeter' | 'informational'
  splitPhaseGroup?: string
}

export interface UsageAuthority {
  configured: boolean
  authorityType?: string
  completeAccount: boolean
  confidence: string
  sourceReference?: string
  aggregateSetId?: string
  deviceIds: string[]
  revision: number
  updatedAt?: string
}

export interface AlertSummary {
  id: string
  title: string
  message: string
  severity: string
  status: string
  openedAt?: string
  sensorId?: string
}

export interface ElectricService {
  id: string
  homeId: string
  name: string
  provider: string
  currency: string
  timezone: string
  billingDay: number
  status: string
  costScope: string
  revision: number
  currentPlan?: string
  planCode?: string
  rateVersionId?: string
  currentAssignmentId?: string
  currentAssignmentRevision?: number
  currentVersion?: number
  currentPeriod?: string
  currentRate?: string
  nextPeriod?: string
  nextRate?: string
  billingStartsAt?: string
  billingEndsAt?: string
  readiness: {
    rate: string
    cost: string
    topologyComplete: boolean
  }
}

export type ConfigurationState =
  | 'ready'
  | 'setup_needed'
  | 'partially_configured'
  | 'waiting_for_data'
  | 'attention_required'
  | 'error'

export interface ConfigurationAction {
  id: string
  label: string
  target: string
}

export interface ConfigurationIssue {
  id: string
  category: string
  state: Exclude<ConfigurationState, 'ready'>
  title: string
  whatIsWrong: string
  whyItMatters: string
  howToFix: string
  blocking: boolean
  action: ConfigurationAction
}

export interface ConfigurationStatus {
  homeId: string
  electricServiceId?: string
  state: ConfigurationState
  label: string
  summary: string
  generatedAt: string
  issues: ConfigurationIssue[]
}

export interface RateAssignmentResult {
  assignmentId: string
  electricServiceId: string
  planId: string
  versionId: string
  version: number
  effectiveFrom: string
  effectiveThrough?: string
  state: 'current' | 'scheduled' | 'historical' | 'cancelled'
  replacedAssignmentId?: string
  recalculationJobId?: string
  warnings: string[]
  serviceRevision: number
  idempotent: boolean
}

export interface CurrentRateAssignment {
  homeId: string
  electricServiceId?: string
  serviceRevision?: number
  assignment?: {
    assignmentId: string
    assignmentRevision: number
    planId?: string
    planCode?: string
    planName?: string
    versionId: string
    version?: number
    pricingModel?: string
    effectiveFrom: string
    effectiveThrough?: string
    state: 'current'
  }
}

export interface TierLine {
  id: string
  name: string
  lowerKwh: string
  upperKwh?: string
  rate: string
  usageKwh?: string
  cost?: string
}

export interface BillingCycleSummary {
  available: boolean
  id?: string
  startsAt?: string
  endsAt?: string
  status?: string
  finalizedAt?: string
  daysRemaining?: number
  currentTier?: string
  currentPeriod?: string
  currentRate?: string
  remainingKwh?: string
  usageKwh?: string
  energyCharge?: string
  projectedUsageKwh?: string
  projectedEnergyCharge?: string
  estimatedBill?: string
  projectedBill?: string
  confidence?: string
  coveragePercent?: string
  recalculationVersion: number
  pricingModel?: HomeSummary['pricingModel']
  tiers: TierLine[]
  warnings: string[]
}

export interface BillSummary {
  id: string
  serviceId?: string
  status: string
  extractionMethod?: string
  createdAt: string
  pageCount: number
  usageKwh?: string
  total?: string
  startsAt?: string
  endsAt?: string
  ratePlanId?: string
  rateVersionId?: string
  blockingWarnings: string[]
}

export type BillFieldConfidence =
  | 'parser_confirmed'
  | 'arithmetic_confirmed'
  | 'high'
  | 'medium'
  | 'low'
  | 'manual_confirmed'
  | 'missing'
  | 'conflict'
  | 'not_applicable'

export interface MissingBillField {
  path: string
  outputKind: string
  state: 'not_found_on_bill' | 'needs_review' | 'not_applicable' | 'conflict' | 'unsupported'
  required: boolean
  reason: string
}

export interface BillFieldEvidence {
  path: string
  outputKind: string
  value: string
  confidence: BillFieldConfidence
  sourcePage?: number
  sourceText?: string
  parserRule?: string
  parserVersion: string
}

export interface NormalizedUtilityBill {
  schemaVersion: string
  parserId: string
  parserVersion: string
  artifact: {
    id: string
    displayFilename: string
    sha256: string
    mimeType: 'application/pdf'
    byteSize?: number
    pageCount: number
    extractionMethod: 'text' | 'ocr' | 'mixed'
    importedAt: string
  }
  utility: {
    name?: string
    documentType?: string
    ratePlanCode?: string
  }
  billingCycle: Record<string, unknown>
  planCandidate: Record<string, unknown>
  lineItems: Array<Record<string, unknown>>
  evidence: BillFieldEvidence[]
  validation: Record<string, unknown>
  warnings: string[]
  missingFields: MissingBillField[]
  ignoredSections: Array<Record<string, unknown>>
  processingStatus: string
}

export interface BillImportDetail extends BillSummary {
  revision: number
  normalized: NormalizedUtilityBill
  displayFilename: string
  utilityName?: string
  documentType?: string
  importedAt: string
  processingStatus: string
  thresholdInterpretation: 'fixed_cycle_threshold' | 'daily_baseline' | 'baseline_multiplier' | 'unknown'
  missingFields: MissingBillField[]
  fields: Array<{
    id: string
    path: string
    label: string
    outputKind: string
    value: string
    confidence: BillFieldConfidence
    sourcePage?: number
    status?: string
    parserRule?: string
    sourceText?: string
  }>
  conflicts: Array<{ id: string; path: string; message: string }>
}

export type BillImportSession = BillImportDetail

export interface RatePlanVersion {
  id: string
  version: number
  status: string
  effectiveFrom?: string
  effectiveThrough?: string
  pricingModel?: string
  integritySha256?: string
  immutable: boolean
  publicationStatus: string
  assignmentStatus: string
  displayStatus: string
  parentVersionId?: string
  lifecycleRevision: number
  removedAt?: string
  removalReason?: string
  assignments: RatePlanAssignment[]
}

export interface RatePlanAssignment {
  id: string
  serviceId: string
  versionId: string
  effectiveFrom: string
  effectiveThrough?: string
  state: string
  revision: number
}

export type DropdownAction =
  | 'rate_assignment.replace_current'
  | 'rate_assignment.make_current'
  | 'rate_assignment.end'
  | 'rate_plan.retire'
  | 'rate_plan.remove'
  | 'rate_plan.restore'
  | 'rate_plan.delete_draft'

export interface RatePlanDependencySummary {
  dependencyToken: string
  activeAssignments: Array<Record<string, unknown>>
  futureAssignments: Array<Record<string, unknown>>
  activeAccountPointers: Array<Record<string, unknown>>
  historicalAssignmentCount: number
  historicalCalculationCount: number
  sourceEvidenceCount: number
  billImportCount: number
  permanentDraftDeletionEligible: boolean
  removalBlocked: boolean
}

export interface RateSource {
  id: string
  name: string
  sourceType: string
  enabled: boolean
  lastSuccessAt?: string
  lastCheckedAt?: string
  consecutiveFailures: number
  candidateCount: number
  lastResult?: RateSourceResult
  displayOrigin: string
  technicalUrl?: string
  parserId?: string
}

export interface RateSourceResult {
  checkId: string
  jobId: string
  outcome: string
  checkedAt?: string
  finishedAt?: string
  durationMs?: number
  httpStatus?: number
  candidateCount: number
  artifactCount: number
  errorCode?: string
  errorDetail?: string
}

export interface RateSourceCheckRun {
  id: string
  status: string
  triggerType: string
  requestedAt?: string
  startedAt?: string
  completedAt?: string
  progress: {
    completed: number
    total: number
    currentSourceId?: string
  }
  sourcesAttempted: number
  successes: number
  failures: number
  candidates: number
  archivedEvidence: number
  error?: { code: string; detail: string }
  items: Array<RateSourceResult & { sourceId: string; sourceName: string }>
}

export interface RateEvidence {
  id: string
  versionId: string
  capturedAt?: string
  relationship: string
  checksum?: string
  displaySource: string
}

export interface RateAdjustment {
  id: string
  component: string
  value: string
  unit: string
  provenance: string
  reason: string
  evidenceReference?: string
  effectiveFrom: string
  effectiveThrough?: string
  enabled: boolean
  status: string
  revision: number
}

export interface HomeLiveStatus {
  connected: boolean
  currentPowerW?: string
  latestDataAt?: string
  reportingSensors: number
  totalSensors: number
}

export interface HomeBillingSnapshot {
  plan?: string
  currentRate?: string
  currentPeriod?: string
  cycleEnergyKwh: string
  cycleEstimatedCost: string
  projectedBill?: string
}

export interface HomeDashboardSummary {
  home: Home
  live: HomeLiveStatus
  billing: HomeBillingSnapshot
  summary: HomeSummary
}

export type HistoryRange = 'today' | '7d' | '30d' | 'cycle' | 'custom'
export type HistoryMetric = 'power' | 'energy' | 'cost' | 'energy_cost'
export type HistoryScope = 'home' | 'sensor'

export interface HistoryFilters {
  range: HistoryRange
  metric: HistoryMetric
  scope: HistoryScope
  sensorId?: string
  customStart?: string
  customEnd?: string
}

export interface HistoryPoint {
  start: string
  end: string
  label: string
  powerW?: string
  energyKwh?: string
  cost?: string
  rate?: string
  period?: string
  tier?: string
  coveragePercent: string
  missing: boolean
}

export interface HistoryView {
  title: string
  points: HistoryPoint[]
  energyKwh?: string
  cost?: string
  averagePowerW?: string
  peakPowerW?: string
  blendedRate?: string
  coveragePercent: string
  contributingSensors: number
  warnings: string[]
  ratePlans: string[]
}

export type FamilyRole = 'Owner' | 'Family Member' | 'Viewer'

export interface FamilyMember {
  id: string
  name: string
  email: string
  role: FamilyRole
  roleIds: string[]
  status: 'active' | 'disabled' | 'removed'
  activeSessions: number
  mfaEnabled: boolean
  protected: boolean
  isSelf: boolean
  revision: number
}

export interface FamilyRoleOption {
  id: string
  name: string
  description: string
  builtIn: boolean
  archived: boolean
  revision: number
  permissions: string[]
  assignedUserCount: number
}

export interface PermissionOption {
  code: string
  group: string
  label: string
  description: string
  highRisk: boolean
}

export interface BackupSummary {
  id: string
  createdAt: string
  completedAt?: string
  status: string
  verifiedAt?: string
  sizeBytes?: number
  encrypted: boolean
  manifestFingerprint?: string
  verificationStartedAt?: string
  verificationCompletedAt?: string
  verificationAttempts: number
  verificationDetails: Record<string, unknown>
  failedStage?: string
  safeErrorCode?: string
  safeErrorSummary?: string
  exitCode?: number
  deletedAt?: string
  deletionReason?: string
  artifactRemovalResult?: string
  preDeletionStatus?: string
  replacedByBackupId?: string
}

export interface AdvancedHealthSummary {
  api: string
  database: string
  migration?: string
  version?: string
  protocol?: string
  worker?: string
}

export type SystemHealthStatus = 'healthy' | 'degraded' | 'unhealthy' | 'unknown'

export interface SystemHealthComponent {
  key: 'api' | 'database' | 'worker' | 'storage' | 'backups' | 'live_data' | 'rate_engine'
  label: string
  status: SystemHealthStatus
  summary: string
  checkedAt: string
  lastSuccessAt?: string
  latencyMs?: number
  details: Record<string, unknown>
  remediation?: {
    label: string
    route?: string
    action?: string
  }
  canRetry: boolean
}

export interface SystemHealth {
  schemaVersion: 'system-health/1.0'
  status: SystemHealthStatus
  checkedAt: string
  components: SystemHealthComponent[]
  versions: Record<string, string | undefined>
  recentEvents: Array<{
    occurredAt: string
    component: string
    status: SystemHealthStatus
    summary: string
  }>
}

export type TestLoadProfile =
  | 'steady'
  | 'home_cycle'
  | 'variable_household'
  | 'evening_peak'
  | 'morning_evening_peaks'
  | 'high_load'
  | 'low_load'
  | 'solar_day'
  | 'custom'

export interface TestModeCostPreview {
  enabled: boolean
  available: boolean
  energyKwh: number
  estimatedEnergyCost?: number
  currency?: string
  ratePlan?: string
  rateVersion?: number
  disclosure: string
}

export interface TestModeState {
  enabled: boolean
  sessionId?: string
  siteId?: string
  startedAt?: string
  expiresAt?: string
  remainingSeconds: number
  sensorCount: number
  onlineSensors: number
  offlineSensors: number
  loadProfile?: TestLoadProfile
  customLoadW?: number
  baseLoadW: number
  variationPercent: number
  sampleIntervalSeconds: number
  costPreviewEnabled: boolean
  paused: boolean
  currentPowerW: number
  totalEnergyKwh: number
  sourceType: 'simulated'
  environment: 'test_mode'
  endedAt?: string
  endReason?: 'disabled' | 'expired'
  isolation: Record<string, boolean>
  costPreview?: TestModeCostPreview
}

export interface TestModeSensor {
  id: string
  name: string
  index: number
  online: boolean
  currentPowerW: number
  energyKwh: number
  loadOverrideW?: number
  sourceType: 'simulated'
  environment: 'test_mode'
}

export interface TestModePoint {
  recordedAt: string
  sensorId: string
  sensorName: string
  online: boolean
  powerW: number
  intervalEnergyKwh: number
  sourceType: 'simulated'
  environment: 'test_mode'
}

export interface EnrollmentCode {
  id: string
  code: string
  expiresAt: string
  name: string
}
