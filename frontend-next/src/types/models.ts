export interface ApiProblem {
  title: string
  detail: string
  status: number
  code: string
  errors?: Array<{ location: string[]; message: string }>
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
  currentPowerW: string
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
  recentPeakW: string
  latestDataAt?: string
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
  state: string
  online: boolean
  currentPowerW?: string
  lastSeenAt?: string
  storageHealthy?: boolean
  wifiDbm?: number
  firmware?: string
  monitoredCircuit: string
  backlog: number
  ctRatingAmps: string
  measurementRole: string
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
  startsAt?: string
  endsAt?: string
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

export interface BillImportDetail extends BillSummary {
  revision: number
  normalized: Record<string, unknown>
  fields: Array<{
    id: string
    path: string
    label: string
    value?: string
    confidence?: string
    sourcePage?: number
    status?: string
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
}

export interface RatePlanAssignment {
  id: string
  serviceId: string
  versionId: string
  effectiveFrom: string
  effectiveThrough?: string
}

export interface RateSource {
  id: string
  name: string
  sourceType: string
  enabled: boolean
  lastSuccessAt?: string
  displayOrigin: string
  technicalUrl?: string
  parserId?: string
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
  name: string
  component: string
  operation: string
  value: string
  unit: string
  scope: string
}

export interface HomeLiveStatus {
  connected: boolean
  currentPowerW: string
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
  status: string
  verifiedAt?: string
  sizeBytes?: number
  encrypted: boolean
}

export interface AdvancedHealthSummary {
  api: string
  database: string
  migration?: string
  version?: string
  protocol?: string
  worker?: string
}

export interface EnrollmentCode {
  id: string
  code: string
  expiresAt: string
  name: string
}
