import type {
  AdvancedHealthSummary,
  AlertSummary,
  BackupSummary,
  BillImportDetail,
  BillSummary,
  BillingCycleSummary,
  CircuitSummary,
  ConfigurationStatus,
  CurrentRateAssignment,
  ElectricService,
  FamilyMember,
  FamilyRoleOption,
  FamilyRole,
  PermissionOption,
  HistoryView,
  Home,
  HomeResolution,
  HomeSummary,
  SensorSummary,
  SystemHealth,
  SystemHealthComponent,
  SystemHealthStatus,
  TestLoadProfile,
  TestModePoint,
  TestModeSensor,
  TestModeState,
  UsageAuthority,
  RateEvidence,
  RatePlanDependencySummary,
  RatePlanAssignment,
  RatePlanVersion,
  RateAdjustment,
  RateAssignmentResult,
  RateSourceCheckRun,
  RateSource,
  UserSession,
} from '../types/models'
import {
  booleanValue,
  numberValue,
  objectList,
  optionalString,
  record,
  records,
  stringList,
  stringValue,
} from './validation'

function optionalNumber(value: unknown): number | undefined {
  if ((typeof value === 'number' || typeof value === 'string') && value !== '' && Number.isFinite(Number(value))) {
    return Number(value)
  }
  return undefined
}

function healthStatus(value: unknown): SystemHealthStatus {
  const status = stringValue(value)
  if (!['healthy', 'degraded', 'unhealthy', 'unknown'].includes(status)) {
    throw new TypeError('system health returned an unknown status')
  }
  return status as SystemHealthStatus
}

function testLoadProfile(value: unknown): TestLoadProfile | undefined {
  const profile = stringValue(value)
  if (!profile) return undefined
  if (![
    'steady',
    'home_cycle',
    'variable_household',
    'evening_peak',
    'morning_evening_peaks',
    'high_load',
    'low_load',
    'solar_day',
    'custom',
  ].includes(profile)) {
    throw new TypeError('sensor test mode returned an unknown load profile')
  }
  return profile as TestLoadProfile
}

export function adaptSession(value: unknown): UserSession {
  const source = record(value, 'session')
  const user = source.user ? record(source.user, 'session.user') : undefined
  return {
    authenticated: booleanValue(source.authenticated),
    bootstrapRequired: booleanValue(source.bootstrap_required),
    expiresAt: optionalString(source.expires_at),
    user: user
      ? {
          id: stringValue(user.id),
          email: stringValue(user.email),
          name: stringValue(user.display_name, 'Home owner'),
          roles: stringList(user.roles),
          permissions: stringList(user.permissions),
          allHomes: booleanValue(user.all_sites),
          homeIds: stringList(user.site_ids),
        }
      : undefined,
  }
}

export function adaptSystemHealth(value: unknown): SystemHealth {
  const source = record(value, 'system health')
  const schemaVersion = stringValue(source.schema_version)
  if (schemaVersion !== 'system-health/1.0') {
    throw new TypeError('The frontend and System Health API use incompatible schemas')
  }
  const components: SystemHealthComponent[] = objectList(source.components).map((item) => {
    const key = stringValue(item.key)
    if (!['api', 'database', 'worker', 'storage', 'backups', 'live_data', 'rate_engine'].includes(key)) {
      throw new TypeError('system health returned an unknown component')
    }
    const remediation = item.remediation ? record(item.remediation, 'health remediation') : undefined
    return {
      key: key as SystemHealthComponent['key'],
      label: stringValue(item.label, key),
      status: healthStatus(item.status),
      summary: stringValue(item.summary),
      checkedAt: stringValue(item.checked_at),
      lastSuccessAt: optionalString(item.last_success_at),
      latencyMs: optionalNumber(item.latency_ms),
      details: item.details ? record(item.details, 'health details') : {},
      remediation: remediation ? {
        label: stringValue(remediation.label),
        route: optionalString(remediation.route),
        action: optionalString(remediation.action),
      } : undefined,
      canRetry: booleanValue(item.can_retry, true),
    }
  })
  const versions = record(source.versions, 'health versions')
  return {
    schemaVersion,
    status: healthStatus(source.status),
    checkedAt: stringValue(source.checked_at),
    components,
    versions: Object.fromEntries(
      Object.entries(versions).map(([key, item]) => [key, optionalString(item)]),
    ),
    recentEvents: objectList(source.recent_events).map((item) => ({
      occurredAt: stringValue(item.occurred_at),
      component: stringValue(item.component),
      status: healthStatus(item.status),
      summary: stringValue(item.summary),
    })),
  }
}

export function adaptTestMode(value: unknown): TestModeState {
  const source = record(value, 'sensor test mode')
  const sourceType = stringValue(source.source_type)
  const environment = stringValue(source.environment)
  if (sourceType !== 'simulated' || environment !== 'test_mode') {
    throw new TypeError('Sensor Test Mode returned an unsafe source classification')
  }
  const rawPreview = source.cost_preview
    ? record(source.cost_preview, 'test mode cost preview')
    : undefined
  return {
    enabled: booleanValue(source.enabled),
    sessionId: optionalString(source.session_id),
    siteId: optionalString(source.site_id),
    startedAt: optionalString(source.started_at),
    expiresAt: optionalString(source.expires_at),
    remainingSeconds: numberValue(source.remaining_seconds),
    sensorCount: numberValue(source.sensor_count),
    onlineSensors: numberValue(source.online_sensors),
    offlineSensors: numberValue(source.offline_sensors),
    loadProfile: testLoadProfile(source.load_profile),
    customLoadW: optionalNumber(source.custom_load_w),
    baseLoadW: numberValue(source.base_load_w, 1000),
    variationPercent: numberValue(source.variation_percent, 20),
    sampleIntervalSeconds: numberValue(source.sample_interval_seconds, 5),
    costPreviewEnabled: booleanValue(source.cost_preview_enabled),
    paused: booleanValue(source.paused),
    currentPowerW: numberValue(source.current_power_w),
    totalEnergyKwh: numberValue(source.total_energy_kwh),
    sourceType: 'simulated',
    environment: 'test_mode',
    endedAt: optionalString(source.ended_at),
    endReason: source.end_reason === 'disabled' || source.end_reason === 'expired'
      ? source.end_reason
      : undefined,
    isolation: Object.fromEntries(
      Object.entries(record(source.isolation, 'test mode isolation')).map(
        ([key, item]) => [key, booleanValue(item)],
      ),
    ),
    costPreview: rawPreview ? {
      enabled: booleanValue(rawPreview.enabled),
      available: booleanValue(rawPreview.available),
      energyKwh: numberValue(rawPreview.energy_kwh),
      estimatedEnergyCost: optionalNumber(rawPreview.estimated_energy_cost),
      currency: optionalString(rawPreview.currency),
      ratePlan: optionalString(rawPreview.rate_plan),
      rateVersion: optionalNumber(rawPreview.rate_version),
      disclosure: stringValue(rawPreview.disclosure),
    } : undefined,
  }
}

export function adaptTestModeSensors(value: unknown): TestModeSensor[] {
  return records(value, 'test mode sensors').map((source) => {
    if (source.source_type !== 'simulated' || source.environment !== 'test_mode') {
      throw new TypeError('A test sensor returned an unsafe source classification')
    }
    return {
      id: stringValue(source.id),
      name: stringValue(source.name),
      index: numberValue(source.index),
      online: booleanValue(source.online),
      currentPowerW: numberValue(source.current_power_w),
      energyKwh: numberValue(source.energy_kwh),
      loadOverrideW: optionalNumber(source.load_override_w),
      sourceType: 'simulated',
      environment: 'test_mode',
    }
  })
}

export function adaptTestModeHistory(value: unknown): TestModePoint[] {
  return records(value, 'test mode history').map((source) => {
    if (source.source_type !== 'simulated' || source.environment !== 'test_mode') {
      throw new TypeError('Test history returned an unsafe source classification')
    }
    return {
      recordedAt: stringValue(source.recorded_at),
      sensorId: stringValue(source.sensor_id),
      sensorName: stringValue(source.sensor_name),
      online: booleanValue(source.online),
      powerW: numberValue(source.power_w),
      intervalEnergyKwh: numberValue(source.interval_energy_kwh),
      sourceType: 'simulated',
      environment: 'test_mode',
    }
  })
}

function adaptHome(source: Record<string, unknown>): Home {
  const lifecycle = stringValue(source.lifecycle_state, 'active')
  return {
    id: stringValue(source.id),
    name: stringValue(source.name, 'Home'),
    timezone: stringValue(source.timezone, 'America/Los_Angeles'),
    currency: stringValue(source.currency, 'USD'),
    lifecycle: lifecycle === 'disabled' || lifecycle === 'removed' ? lifecycle : 'active',
    isDefault: booleanValue(source.is_default),
    revision: numberValue(source.revision),
    provider: optionalString(source.organization),
  }
}

export function resolveSingleHome(value: unknown): HomeResolution {
  const homes = records(value, 'homes').map(adaptHome).filter((home) => home.lifecycle === 'active')
  if (homes.length === 0) return { state: 'missing' }
  if (homes.length > 1) return { state: 'multiple', homes }
  const home = homes.find((item) => item.isDefault) ?? homes[0]
  if (!home) return { state: 'missing' }
  return { state: 'ready', home }
}

export function adaptSensors(value: unknown): SensorSummary[] {
  const onlineStates = new Set([
    'online_synchronized',
    'online_with_backlog',
    'online_push_only',
    'api_healthy_meter_failed',
    'api_healthy_storage_failed',
    'time_unsynchronized',
  ])
  const freshnessStates = new Set([
    'live',
    'waiting',
    'stale',
    'offline',
    'unavailable',
    'invalid',
    'needs_attention',
  ])
  return records(value, 'sensors').map((source) => {
    const deviceStatus = stringValue(source.status, 'unknown')
    const rawFreshness = stringValue(source.measurement_freshness)
    const measurementFreshness = freshnessStates.has(rawFreshness)
      ? rawFreshness as SensorSummary['measurementFreshness']
      : onlineStates.has(deviceStatus)
        ? 'waiting'
        : 'offline'
    return {
      id: stringValue(source.id),
      name: stringValue(source.name, 'Unnamed sensor'),
      homeId: stringValue(source.site_id),
      circuitId: optionalString(source.circuit_id),
      utilityAccountId: optionalString(source.utility_account_id),
      state: measurementFreshness,
      deviceStatus,
      online: onlineStates.has(deviceStatus),
      currentPowerW: optionalString(source.current_watts),
      voltageVolts: optionalString(source.voltage_volts),
      currentAmps: optionalString(source.current_amps),
      frequencyHz: optionalString(source.frequency_hz),
      powerFactor: optionalString(source.power_factor),
      latestMeasurementAt: optionalString(source.latest_measurement_at),
      measurementReceivedAt: optionalString(source.measurement_received_at),
      measurementSequence: optionalNumber(source.measurement_sequence),
      measurementSource: source.measurement_source === 'heartbeat_live'
        || source.measurement_source === 'committed_reading'
        ? source.measurement_source
        : undefined,
      measurementFreshness,
      invalidMetrics: stringList(source.measurement_invalid_metrics),
      lastSeenAt: optionalString(source.last_seen_at),
      storageHealthy: typeof source.sd_ok === 'boolean' ? source.sd_ok : undefined,
      wifiDbm: typeof source.rssi_dbm === 'number' ? source.rssi_dbm : undefined,
      firmware: optionalString(source.firmware_version),
      monitoredCircuit: stringValue(source.circuit_name, 'Unassigned'),
      includedInDefault: booleanValue(source.included_in_default),
      backlog: numberValue(source.backlog),
      ctRatingAmps: stringValue(source.ct_rating_amps, '100'),
      measurementRole: stringValue(source.measurement_role, 'submeter'),
    }
  })
}

export function adaptCircuits(value: unknown): CircuitSummary[] {
  return records(value, 'circuits').map((source) => {
    const role = stringValue(source.measurement_role, 'branch')
    if (!['main', 'service-leg', 'branch', 'submeter', 'informational'].includes(role)) {
      throw new Error('Circuit returned an unsupported measurement role')
    }
    return {
      id: stringValue(source.id),
      homeId: stringValue(source.site_id),
      parentId: optionalString(source.parent_id),
      name: stringValue(source.name, 'Unnamed circuit'),
      measurementRole: role as CircuitSummary['measurementRole'],
      splitPhaseGroup: optionalString(source.split_phase_group),
    }
  })
}

export function adaptUsageAuthority(value: unknown): UsageAuthority {
  const source = record(value, 'usage authority')
  return {
    configured: booleanValue(source.configured),
    authorityType: optionalString(source.authority_type),
    completeAccount: booleanValue(source.complete_account),
    confidence: stringValue(source.confidence, 'unknown'),
    sourceReference: optionalString(source.source_reference),
    aggregateSetId: optionalString(source.aggregate_set_id),
    deviceIds: stringList(source.device_ids),
    revision: numberValue(source.revision),
    updatedAt: optionalString(source.updated_at),
  }
}

export function adaptAlerts(value: unknown): AlertSummary[] {
  return records(value, 'alerts').map((source) => ({
    id: stringValue(source.id),
    title: stringValue(source.title, stringValue(source.rule_name, 'Home alert')),
    message: stringValue(source.message, stringValue(source.summary, 'Review this alert.')),
    severity: stringValue(source.severity, 'info'),
    status: stringValue(source.status, 'active'),
    openedAt: optionalString(source.opened_at),
    sensorId: optionalString(source.device_id),
  }))
}

export function adaptElectricServices(value: unknown): ElectricService[] {
  return records(value, 'electric services').map((source) => {
    const context = source.rate_context ? record(source.rate_context, 'rate context') : {}
    const cycle = context.billing_cycle ? record(context.billing_cycle, 'billing cycle') : {}
    const readiness = source.readiness ? record(source.readiness, 'readiness') : {}
    return {
      id: stringValue(source.id),
      homeId: stringValue(source.site_id),
      name: stringValue(source.nickname, stringValue(source.name, 'Electric service')),
      provider: stringValue(source.utility_name, stringValue(source.generation_provider, 'Electric utility')),
      currency: stringValue(source.currency, 'USD'),
      timezone: stringValue(source.timezone, 'America/Los_Angeles'),
      billingDay: numberValue(source.billing_cycle_start_day, 1),
      status: stringValue(source.status, 'active'),
      costScope: stringValue(source.cost_scope, 'energy_only'),
      revision: numberValue(source.revision),
      currentPlan: optionalString(context.current_plan),
      planCode: optionalString(context.plan_code),
      rateVersionId: optionalString(context.rate_version_id),
      currentAssignmentId: optionalString(context.current_assignment_id),
      currentAssignmentRevision: typeof context.current_assignment_revision === 'number'
        ? context.current_assignment_revision
        : undefined,
      currentVersion: typeof context.current_version === 'number' ? context.current_version : undefined,
      currentPeriod: optionalString(context.current_period),
      currentRate: optionalString(context.current_price_per_kwh),
      nextPeriod: optionalString(context.next_period),
      nextRate: optionalString(context.next_price_per_kwh),
      billingStartsAt: optionalString(cycle.starts_at),
      billingEndsAt: optionalString(cycle.ends_at),
      readiness: {
        rate: stringValue(readiness.rate, 'missing'),
        cost: stringValue(readiness.cost, 'missing'),
        topologyComplete: booleanValue(readiness.topology_complete),
      },
    }
  })
}

export function adaptConfigurationStatus(value: unknown): ConfigurationStatus {
  const source = record(value, 'configuration status')
  const allowedStates = new Set([
    'ready',
    'setup_needed',
    'partially_configured',
    'waiting_for_data',
    'attention_required',
    'error',
  ])
  const state = stringValue(source.state)
  if (!allowedStates.has(state)) throw new Error('Configuration status returned an unknown state')
  return {
    homeId: stringValue(source.home_id),
    electricServiceId: optionalString(source.electric_service_id),
    state: state as ConfigurationStatus['state'],
    label: stringValue(source.label),
    summary: stringValue(source.summary),
    generatedAt: stringValue(source.generated_at),
    issues: objectList(source.issues).map((item) => {
      const issueState = stringValue(item.state)
      if (issueState === 'ready' || !allowedStates.has(issueState)) {
        throw new Error('Configuration issue returned an unknown state')
      }
      const action = record(item.action, 'configuration action')
      return {
        id: stringValue(item.id),
        category: stringValue(item.category),
        state: issueState as ConfigurationStatus['issues'][number]['state'],
        title: stringValue(item.title),
        whatIsWrong: stringValue(item.what_is_wrong),
        whyItMatters: stringValue(item.why_it_matters),
        howToFix: stringValue(item.how_to_fix),
        blocking: booleanValue(item.blocking),
        action: {
          id: stringValue(action.id),
          label: stringValue(action.label),
          target: stringValue(action.target),
        },
      }
    }),
  }
}

export function adaptRateAssignmentResult(value: unknown): RateAssignmentResult {
  const source = record(value, 'rate assignment result')
  if (stringValue(source.schema_version) !== 'rate-assignment-result/1.0') {
    throw new Error('Rate assignment response uses an unsupported schema')
  }
  const state = stringValue(source.state)
  if (!['current', 'scheduled', 'historical', 'cancelled'].includes(state)) {
    throw new Error('Rate assignment response returned an unknown state')
  }
  return {
    assignmentId: stringValue(source.assignment_id),
    electricServiceId: stringValue(source.electric_service_id),
    planId: stringValue(source.plan_id),
    versionId: stringValue(source.version_id),
    version: numberValue(source.version),
    effectiveFrom: stringValue(source.effective_from),
    effectiveThrough: optionalString(source.effective_to),
    state: state as RateAssignmentResult['state'],
    replacedAssignmentId: optionalString(source.replaced_assignment_id),
    recalculationJobId: optionalString(source.recalculation_job_id),
    warnings: stringList(source.warnings),
    serviceRevision: numberValue(source.service_revision),
    idempotent: booleanValue(source.idempotent),
  }
}

export function adaptCurrentRateAssignment(value: unknown): CurrentRateAssignment {
  const source = record(value, 'current rate assignment')
  if (stringValue(source.schema_version) !== 'current-rate-assignment/1.0') {
    throw new Error('Current assignment response uses an unsupported schema')
  }
  const assignment = source.assignment ? record(source.assignment, 'current assignment') : undefined
  if (assignment && stringValue(assignment.state) !== 'current') {
    throw new Error('Current assignment response returned a non-current state')
  }
  return {
    homeId: stringValue(source.home_id),
    electricServiceId: optionalString(source.electric_service_id),
    serviceRevision: typeof source.service_revision === 'number'
      ? source.service_revision
      : undefined,
    assignment: assignment ? {
      assignmentId: stringValue(assignment.assignment_id),
      assignmentRevision: numberValue(assignment.assignment_revision),
      planId: optionalString(assignment.plan_id),
      planCode: optionalString(assignment.plan_code),
      planName: optionalString(assignment.plan_name),
      versionId: stringValue(assignment.version_id),
      version: typeof assignment.version === 'number' ? assignment.version : undefined,
      pricingModel: optionalString(assignment.pricing_model),
      effectiveFrom: stringValue(assignment.effective_from),
      effectiveThrough: optionalString(assignment.effective_to),
      state: 'current',
    } : undefined,
  }
}

export function adaptBillingCycle(value: unknown): BillingCycleSummary {
  const source = record(value, 'billing cycle')
  const cycle = source.cycle ? record(source.cycle, 'cycle') : {}
  const currentTier = source.current_tier ? record(source.current_tier, 'current tier') : {}
  return {
    available: booleanValue(source.available),
    id: optionalString(cycle.id),
    startsAt: optionalString(cycle.starts_at),
    endsAt: optionalString(cycle.ends_at),
    status: optionalString(cycle.status),
    finalizedAt: optionalString(cycle.finalized_at),
    daysRemaining: typeof cycle.days_remaining === 'number' ? cycle.days_remaining : undefined,
    currentTier: optionalString(currentTier.name),
    currentPeriod: optionalString(source.current_rate_period),
    currentRate: optionalString(source.current_energy_price),
    remainingKwh: optionalString(source.remaining_kwh),
    usageKwh: optionalString(source.authoritative_usage_kwh),
    energyCharge: optionalString(source.energy_charge),
    projectedUsageKwh: optionalString(source.projected_usage_kwh),
    projectedEnergyCharge: optionalString(source.projected_energy_charge),
    estimatedBill: optionalString(source.estimated_total_bill),
    projectedBill: optionalString(source.projected_total_bill),
    confidence: optionalString(source.projection_confidence),
    coveragePercent: optionalString(source.coverage_percent),
    recalculationVersion: numberValue(source.recalculation_version),
    pricingModel: optionalString(source.pricing_model) as BillingCycleSummary['pricingModel'],
    tiers: objectList(source.tiers).map((tier) => ({
      id: stringValue(tier.tier_id),
      name: stringValue(tier.name, 'Tier'),
      lowerKwh: stringValue(tier.lower_bound_kwh, '0'),
      upperKwh: optionalString(tier.upper_bound_kwh),
      rate: stringValue(tier.price_per_kwh, '0'),
      usageKwh: optionalString(tier.usage_kwh),
      cost: optionalString(tier.energy_charge),
    })),
    warnings: stringList(source.warnings),
  }
}

export function adaptHomeSummary(
  fleetValue: unknown,
  sensors: SensorSummary[],
  service?: ElectricService,
  cycle?: BillingCycleSummary,
): HomeSummary {
  const fleet = record(fleetValue, 'home summary')
  return {
    currentPowerW: optionalString(fleet.current_load_w),
    energyTodayKwh: stringValue(fleet.energy_today_kwh, '0'),
    estimatedCostToday: stringValue(fleet.estimated_cost_today, '0'),
    cycleEnergyKwh: stringValue(fleet.billing_cycle_energy_kwh, cycle?.usageKwh ?? '0'),
    cycleEstimatedCost: stringValue(fleet.estimated_billing_cycle_cost, cycle?.energyCharge ?? '0'),
    projectedBill: cycle?.projectedBill,
    cycleDaysRemaining: cycle?.daysRemaining,
    cycleConfidence: cycle?.confidence,
    onlineSensors: sensors.filter((sensor) => sensor.online).length,
    reportingSensors: numberValue(fleet.reporting_devices),
    totalSensors: sensors.length,
    attentionSensors: sensors.filter((sensor) => !sensor.online || sensor.storageHealthy === false).length,
    activeAlerts: numberValue(fleet.active_alerts),
    recentPeakW: optionalString(fleet.recent_peak_w),
    latestDataAt: optionalString(fleet.latest_data_at)
      ?? optionalString(fleet.latest_measurement_at),
    latestReceivedAt: optionalString(fleet.latest_received_at),
    serverNow: optionalString(fleet.server_now),
    coveragePercent: optionalString(fleet.coverage_percent),
    hasLiveData: booleanValue(fleet.has_live_data),
    hasEnergyData: booleanValue(fleet.has_energy_data),
    hasCostData: booleanValue(fleet.has_cost_data),
    currentPlan: service?.currentPlan ?? optionalString(fleet.current_rate_plan),
    currentRate: service?.currentRate ?? optionalString(fleet.current_rate_price_per_kwh) ?? cycle?.currentRate,
    currentPeriod: service?.currentPeriod ?? optionalString(fleet.current_tou_bucket) ?? cycle?.currentPeriod,
    nextPeriod: service?.nextPeriod,
    nextRate: service?.nextRate,
    nextPeriodAt: undefined,
    currentTier: cycle?.currentTier,
    remainingTierKwh: cycle?.remainingKwh,
    tierProgressPercent: cycle?.coveragePercent,
    pricingModel: cycle?.pricingModel,
    disclosure: stringValue(fleet.disclosure, 'Energy costs are estimates and may differ from the utility bill.'),
  }
}

export function adaptHistory(value: unknown): HistoryView {
  const source = record(value, 'history')
  const scope = record(source.scope, 'history scope')
  const summary = record(source.summary, 'history summary')
  const combined = objectList(source.combined)
  const individual = objectList(source.individual)
  const points = combined.length
    ? combined
    : individual.flatMap((series) => objectList(series.points))
  return {
    title: stringValue(scope.display_name, 'Whole Home'),
    points: points.map((point) => ({
      start: stringValue(point.interval_start_utc),
      end: stringValue(point.interval_end_utc),
      label: stringValue(point.local_start, stringValue(point.interval_start_utc)),
      powerW: optionalString(point.average_power_w),
      energyKwh: optionalString(point.energy_kwh),
      cost: optionalString(point.energy_cost),
      rate: optionalString(point.rate_per_kwh),
      period: optionalString(point.tou_period),
      tier: objectList(point.rate_contributions)[0]
        ? optionalString(objectList(point.rate_contributions)[0]?.tier_name)
        : undefined,
      coveragePercent: stringValue(point.coverage_percent, '0'),
      missing: numberValue(point.contributing_sensor_count) < numberValue(point.included_sensor_count),
    })),
    energyKwh: optionalString(summary.energy_kwh),
    cost: optionalString(summary.energy_cost),
    averagePowerW: optionalString(summary.average_power_w),
    peakPowerW: optionalString(summary.peak_power_w),
    blendedRate: optionalString(summary.blended_rate_per_kwh),
    coveragePercent: stringValue(summary.coverage_percent, '0'),
    contributingSensors: numberValue(summary.contributing_sensor_count),
    warnings: objectList(source.warnings).map((warning) => stringValue(warning.message)).filter(Boolean),
    ratePlans: objectList(source.rate_versions_used).map((version) => stringValue(version.rate_plan_name)).filter(Boolean),
  }
}

export function adaptBills(value: unknown): BillSummary[] {
  return records(value, 'bills').map((source) => {
    const cycle = source.billing_cycle ? record(source.billing_cycle, 'bill cycle') : {}
    return {
      id: stringValue(source.id),
      serviceId: optionalString(source.utility_account_id),
      status: stringValue(source.status, 'processing'),
      extractionMethod: optionalString(source.extraction_method),
      createdAt: stringValue(source.created_at),
      pageCount: numberValue(source.page_count),
      usageKwh: optionalString(cycle.total_usage_kwh),
      total: optionalString(cycle.estimated_total),
      startsAt: optionalString(cycle.starts_at),
      endsAt: optionalString(cycle.ends_at),
      ratePlanId: optionalString(source.rate_plan_id),
      rateVersionId: optionalString(source.rate_version_id),
      blockingWarnings: stringList(source.blocking_warnings),
    }
  })
}

export function adaptRatePlanDependencies(value: unknown): RatePlanDependencySummary {
  const source = record(value, 'rate plan dependency summary')
  const dependencyToken = stringValue(source.dependency_token)
  if (!/^[a-f0-9]{64}$/u.test(dependencyToken)) {
    throw new Error('Rate plan dependency summary did not include a valid concurrency token')
  }
  return {
    dependencyToken,
    activeAssignments: objectList(source.active_assignments),
    futureAssignments: objectList(source.future_assignments),
    activeAccountPointers: objectList(source.active_account_pointers),
    historicalAssignmentCount: numberValue(source.historical_assignment_count),
    historicalCalculationCount: numberValue(source.historical_calculation_count),
    sourceEvidenceCount: numberValue(source.source_evidence_count),
    billImportCount: numberValue(source.bill_import_count),
    permanentDraftDeletionEligible: booleanValue(source.permanent_draft_deletion_eligible),
    removalBlocked: booleanValue(source.removal_blocked),
  }
}

export function adaptBillDetail(value: unknown): BillImportDetail {
  const source = record(value, 'bill')
  const cycle = source.cycle_draft
    ? record(source.cycle_draft, 'bill cycle')
    : source.billing_cycle
      ? record(source.billing_cycle, 'bill cycle')
      : {}
  const normalized = record(source.normalized_artifact, 'normalized utility bill')
  const artifact = record(normalized.artifact, 'normalized bill artifact')
  const utility = normalized.utility ? record(normalized.utility, 'normalized bill utility') : {}
  const normalizedCycle = normalized.billing_cycle
    ? record(normalized.billing_cycle, 'normalized billing cycle')
    : {}
  const planCandidate = normalized.plan_candidate
    ? record(normalized.plan_candidate, 'normalized plan candidate')
    : {}
  const extractedFields = objectList(source.fields)
  const allowedConfidence = new Set([
    'parser_confirmed',
    'arithmetic_confirmed',
    'high',
    'medium',
    'low',
    'manual_confirmed',
    'missing',
    'conflict',
    'not_applicable',
  ])
  const confidence = (input: unknown): BillImportDetail['fields'][number]['confidence'] => {
    const result = stringValue(input)
    if (!allowedConfidence.has(result)) throw new Error(`Unsupported bill confidence state: ${result || 'empty'}`)
    return result as BillImportDetail['fields'][number]['confidence']
  }
  const warningMessages = (input: unknown) => objectList(input)
    .map((warning) => stringValue(warning.message))
    .filter(Boolean)
  const missingFields = objectList(normalized.missing_fields).map((field) => {
    const rawState = stringValue(field.state, 'not_found_on_bill')
    const state = ['not_found_on_bill', 'needs_review', 'not_applicable', 'conflict', 'unsupported'].includes(rawState)
      ? rawState as BillImportDetail['missingFields'][number]['state']
      : 'not_found_on_bill'
    return {
      path: stringValue(field.field),
      outputKind: stringValue(field.output_kind),
      state,
      required: booleanValue(field.required),
      reason: stringValue(field.reason, 'The bill did not contain this field.'),
    }
  })
  const fields = extractedFields
    .filter((field) => field.effective_value !== null && field.effective_value !== undefined && stringValue(field.effective_value) !== '')
    .map((field) => ({
      id: stringValue(field.id),
      path: stringValue(field.field_key),
      label: stringValue(field.field_key).replaceAll('_', ' '),
      outputKind: stringValue(field.output_kind),
      value: stringValue(field.effective_value),
      confidence: confidence(field.confidence),
      sourcePage: typeof field.page_number === 'number' ? field.page_number : undefined,
      status: optionalString(field.review_state),
      parserRule: optionalString(field.parser_rule),
      sourceText: optionalString(field.source_excerpt),
    }))
  return {
    id: stringValue(source.id),
    serviceId: optionalString(source.utility_account_id),
    status: stringValue(source.status, 'processing'),
    extractionMethod: optionalString(source.extraction_method),
    createdAt: stringValue(source.created_at),
    pageCount: numberValue(source.page_count),
    usageKwh: optionalString(cycle.total_usage_kwh),
    total: optionalString(cycle.full_bill_total),
    startsAt: optionalString(cycle.starts_at),
    endsAt: optionalString(cycle.ends_at),
    ratePlanId: optionalString(source.rate_plan_id),
    rateVersionId: optionalString(source.rate_version_id),
    blockingWarnings: warningMessages(source.blocking_warnings),
    revision: numberValue(source.revision),
    normalized: {
      schemaVersion: stringValue(normalized.schema_version),
      parserId: stringValue(normalized.parser_id),
      parserVersion: stringValue(normalized.parser_version),
      artifact: {
        id: stringValue(artifact.artifact_id),
        displayFilename: stringValue(artifact.display_filename, 'utility-bill.pdf'),
        sha256: stringValue(artifact.sha256),
        mimeType: stringValue(artifact.mime_type) === 'application/pdf' ? 'application/pdf' : (() => { throw new Error('Normalized bill artifact is not a PDF') })(),
        byteSize: typeof artifact.byte_size === 'number' ? artifact.byte_size : undefined,
        pageCount: numberValue(artifact.page_count),
        extractionMethod: ['text', 'ocr', 'mixed'].includes(stringValue(artifact.extraction_method))
          ? stringValue(artifact.extraction_method) as 'text' | 'ocr' | 'mixed'
          : (() => { throw new Error('Unsupported bill extraction method') })(),
        importedAt: stringValue(artifact.imported_at, stringValue(source.created_at)),
      },
      utility: {
        name: optionalString(utility.name),
        documentType: optionalString(utility.document_type),
        ratePlanCode: optionalString(utility.rate_plan_code),
      },
      billingCycle: normalizedCycle,
      planCandidate,
      lineItems: objectList(normalized.line_items),
      evidence: objectList(normalized.evidence).map((item) => ({
        path: stringValue(item.field),
        outputKind: stringValue(item.output_kind),
        value: stringValue(item.value),
        confidence: confidence(item.confidence),
        sourcePage: typeof item.source_page === 'number' ? item.source_page : undefined,
        sourceText: optionalString(item.source_text),
        parserRule: optionalString(item.parser_rule),
        parserVersion: stringValue(item.parser_version, stringValue(normalized.parser_version)),
      })),
      validation: normalized.validation ? record(normalized.validation, 'bill validation') : {},
      warnings: warningMessages(normalized.warnings),
      missingFields,
      ignoredSections: objectList(normalized.ignored_sections),
      processingStatus: stringValue(normalized.processing_status, stringValue(source.status)),
    },
    displayFilename: stringValue(artifact.display_filename, 'utility-bill.pdf'),
    utilityName: optionalString(utility.name),
    documentType: optionalString(utility.document_type),
    importedAt: stringValue(artifact.imported_at, stringValue(source.created_at)),
    processingStatus: stringValue(normalized.processing_status, stringValue(source.status)),
    thresholdInterpretation: ['fixed_cycle_threshold', 'daily_baseline', 'baseline_multiplier'].includes(stringValue(planCandidate.threshold_interpretation))
      ? stringValue(planCandidate.threshold_interpretation) as BillImportDetail['thresholdInterpretation']
      : 'unknown',
    missingFields,
    fields,
    conflicts: objectList(source.conflicts).map((conflict) => ({
      id: stringValue(conflict.id),
      path: stringValue(conflict.field_key),
      message: stringValue(conflict.field_key) === 'pricing_model'
        ? `Uploaded bill: ${pricingModelLabel(stringValue(conflict.extracted_value, 'not provided'))} · current plan: ${pricingModelLabel(stringValue(conflict.configured_value, 'not provided'))}. Confirming uses the bill value for the new draft and preserves existing history.`
        : `Uploaded bill: ${stringValue(conflict.extracted_value, 'not provided')} · current setup: ${stringValue(conflict.configured_value, 'not provided')}`,
    })),
  }
}

function homeownerRole(roles: string[]): FamilyRole {
  if (roles.some((role) => role === 'admin' || role.toLowerCase().includes('owner'))) return 'Owner'
  if (roles.some((role) => role === 'operator' || role === 'rate-manager')) return 'Family Member'
  return 'Viewer'
}

export function adaptFamily(value: unknown, currentUserId?: string): FamilyMember[] {
  const source = record(value, 'family')
  return objectList(source.users).map((user) => {
    const roles = stringList(user.roles)
    return {
      id: stringValue(user.id),
      name: stringValue(user.display_name, 'Family member'),
      email: stringValue(user.email),
      role: homeownerRole(roles),
      roleIds: roles,
      status: ['disabled', 'removed'].includes(stringValue(user.status))
        ? stringValue(user.status) as 'disabled' | 'removed'
        : 'active',
      activeSessions: numberValue(user.active_session_count),
      mfaEnabled: booleanValue(user.mfa_enabled),
      protected: booleanValue(user.protected_account) || booleanValue(user.protected_administrator),
      isSelf: stringValue(user.id) === currentUserId,
      revision: numberValue(user.access_revision, 1),
    }
  })
}

export function adaptFamilyRoles(value: unknown): FamilyRoleOption[] {
  const source = record(value, 'family roles')
  return objectList(source.roles).map((role) => ({
    id: stringValue(role.id),
    name: stringValue(role.display_name, stringValue(role.id, 'Role')),
    description: stringValue(role.description, 'Custom access role'),
    builtIn: booleanValue(role.built_in),
    archived: booleanValue(role.archived),
    revision: numberValue(role.revision, 1),
    permissions: stringList(role.permissions),
    assignedUserCount: numberValue(role.assigned_user_count),
  }))
}

export function adaptPermissions(value: unknown): PermissionOption[] {
  const source = record(value, 'permission catalog')
  return objectList(source.permissions).map((permission) => ({
    code: stringValue(permission.code),
    group: stringValue(permission.group, 'Other'),
    label: stringValue(permission.label, stringValue(permission.code)),
    description: stringValue(permission.description),
    highRisk: booleanValue(permission.high_risk),
  }))
}

export function adaptBackups(value: unknown): BackupSummary[] {
  return records(value, 'backups').map((backup) => {
    const verificationDetails = backup.verification_details
      ? record(backup.verification_details, 'backup verification details')
      : {}
    return {
      id: stringValue(backup.id),
      createdAt: stringValue(backup.started_at, stringValue(backup.created_at)),
      completedAt: optionalString(backup.completed_at),
      status: stringValue(backup.status, 'unknown'),
      verifiedAt: optionalString(backup.verified_at),
      sizeBytes: typeof backup.size_bytes === 'number' ? backup.size_bytes : undefined,
      encrypted: booleanValue(backup.encrypted),
      manifestFingerprint: optionalString(backup.manifest_hash),
      verificationStartedAt: optionalString(backup.verification_started_at),
      verificationCompletedAt: optionalString(backup.verification_completed_at),
      verificationAttempts: numberValue(backup.verification_attempt_count),
      verificationDetails,
      failedStage: optionalString(backup.failed_stage),
      safeErrorCode: optionalString(backup.safe_error_code),
      safeErrorSummary: optionalString(backup.safe_error_summary),
      exitCode: typeof backup.exit_code === 'number' ? backup.exit_code : undefined,
      deletedAt: optionalString(backup.deleted_at),
      deletionReason: optionalString(backup.deletion_reason),
      artifactRemovalResult: optionalString(backup.artifact_removal_result),
    }
  })
}

export function adaptHealth(value: unknown): AdvancedHealthSummary {
  const source = record(value, 'health')
  const checks = source.checks ? record(source.checks, 'health checks') : {}
  return {
    api: stringValue(source.status, 'unknown'),
    database: stringValue(checks.database, 'unknown'),
    migration: optionalString(checks.migration),
    version: optionalString(source.version),
    protocol: optionalString(source.protocol_version),
    worker: optionalString(source.worker_status),
  }
}

export function adaptRateVersions(value: unknown): RatePlanVersion[] {
  const rows = Array.isArray(value) ? objectList(value) : objectList(record(value, 'rate versions').versions)
  return rows.map((item) => {
    const publicationStatus = stringValue(item.publication_status, stringValue(item.status, 'draft'))
    const assignmentStatus = stringValue(item.assignment_status, 'unassigned')
    return {
      id: stringValue(item.id),
      version: numberValue(item.version),
      status: publicationStatus,
      publicationStatus,
      assignmentStatus,
      displayStatus: stringValue(item.display_status, assignmentStatus === 'unassigned' ? publicationStatus : assignmentStatus),
      effectiveFrom: optionalString(item.effective_from),
      effectiveThrough: optionalString(item.effective_through) ?? optionalString(item.effective_to),
      pricingModel: optionalString(item.pricing_model),
      integritySha256: optionalString(item.integrity_sha256) ?? optionalString(item.content_hash),
      immutable: booleanValue(item.immutable) || booleanValue(item.immutable_after_use),
      parentVersionId: optionalString(item.parent_version_id),
      lifecycleRevision: numberValue(item.lifecycle_revision, 1),
      removedAt: optionalString(item.removed_at),
      removalReason: optionalString(item.removal_reason),
      assignments: adaptRateAssignments(objectList(item.assignments)),
    }
  })
}

export function adaptRateAssignments(value: unknown): RatePlanAssignment[] {
  const rows = Array.isArray(value) ? objectList(value) : objectList(record(value, 'rate assignments').assignments)
  return rows.map((item) => ({
    id: stringValue(item.id),
    serviceId: stringValue(item.utility_account_id),
    versionId: stringValue(item.rate_version_id),
    effectiveFrom: stringValue(item.effective_from),
    effectiveThrough: optionalString(item.effective_to),
    state: stringValue(item.state, 'historical'),
    revision: numberValue(item.revision, 1),
  }))
}

export function adaptRateSources(value: unknown): RateSource[] {
  const rows = objectList(record(value, 'rate sources').sources)
  return rows.map((item) => {
    const technicalUrl = stringValue(item.url)
    const parserId = stringValue(item.parser_id)
    return {
      id: stringValue(item.id),
      name: stringValue(item.name, 'Managed source'),
      sourceType: friendlySourceType(parserId),
      enabled: item.enabled !== false,
      lastSuccessAt: optionalString(item.last_success_at),
      lastCheckedAt: optionalString(item.last_checked_at),
      consecutiveFailures: numberValue(item.consecutive_failures),
      candidateCount: numberValue(item.candidate_count),
      lastResult: item.last_result ? adaptRateSourceResult(record(item.last_result)) : undefined,
      displayOrigin: friendlySourceOrigin(technicalUrl),
      technicalUrl,
      parserId,
    }
  })
}

function adaptRateSourceResult(
  item: Record<string, unknown>,
): NonNullable<RateSource['lastResult']> {
  return {
    checkId: stringValue(item.check_id, stringValue(item.id)),
    jobId: stringValue(item.job_id),
    outcome: stringValue(item.outcome, 'unknown'),
    checkedAt: optionalString(item.checked_at),
    finishedAt: optionalString(item.finished_at),
    durationMs: typeof item.duration_ms === 'number' ? item.duration_ms : undefined,
    httpStatus: typeof item.http_status === 'number' ? item.http_status : undefined,
    candidateCount: numberValue(item.candidate_count),
    artifactCount: numberValue(item.artifact_count),
    errorCode: optionalString(item.error_code),
    errorDetail: optionalString(item.error_detail),
  }
}

export function adaptRateSourceCheckRun(value: unknown): RateSourceCheckRun {
  const source = record(value, 'rate source check run')
  const progress = source.progress ? record(source.progress, 'source check progress') : {}
  return {
    id: stringValue(source.id),
    status: stringValue(source.status, 'queued'),
    triggerType: stringValue(source.trigger_type, 'manual'),
    requestedAt: optionalString(source.requested_at),
    startedAt: optionalString(source.started_at),
    completedAt: optionalString(source.completed_at),
    progress: {
      completed: numberValue(progress.completed),
      total: numberValue(progress.total),
      currentSourceId: optionalString(progress.current_source_id),
    },
    sourcesAttempted: numberValue(source.sources_attempted),
    successes: numberValue(source.successes),
    failures: numberValue(source.failures),
    candidates: numberValue(source.candidates),
    archivedEvidence: numberValue(source.archived_evidence),
    error: source.error
      ? {
          code: stringValue(record(source.error).code),
          detail: stringValue(record(source.error).detail),
        }
      : undefined,
    items: objectList(source.items).map((item) => ({
      ...adaptRateSourceResult(item),
      sourceId: stringValue(item.source_id),
      sourceName: stringValue(item.source_name, 'Managed source'),
    })),
  }
}

export function adaptRateSourceCheckRuns(value: unknown): RateSourceCheckRun[] {
  return (Array.isArray(value) ? value : objectList(record(value).runs)).map(
    adaptRateSourceCheckRun,
  )
}

export function adaptRateAdjustments(value: unknown): RateAdjustment[] {
  return (Array.isArray(value) ? objectList(value) : objectList(record(value).adjustments)).map(
    (item) => ({
      id: stringValue(item.id),
      component: stringValue(item.component),
      value: stringValue(item.value),
      unit: stringValue(item.unit),
      provenance: stringValue(item.provenance),
      reason: stringValue(item.reason),
      evidenceReference: optionalString(item.evidence_reference),
      effectiveFrom: stringValue(item.effective_from),
      effectiveThrough: optionalString(item.effective_to),
      enabled: item.enabled !== false,
      status: stringValue(item.status, 'active'),
      revision: numberValue(item.revision, 1),
    }),
  )
}

export function adaptRateEvidence(value: unknown): RateEvidence[] {
  const rows = Array.isArray(value) ? objectList(value) : objectList(record(value, 'rate evidence').source_evidence)
  return rows.map((item) => ({
    id: stringValue(item.artifact_id, stringValue(item.id)),
    versionId: stringValue(item.rate_version_id),
    capturedAt: optionalString(item.captured_at),
    relationship: stringValue(item.relationship, 'supporting'),
    checksum: optionalString(item.sha256),
    displaySource: friendlySourceType(stringValue(item.parser_id)),
  }))
}

function friendlySourceOrigin(value: string): string {
  if (value.startsWith('urn:power-monitor:utility-bill:')) return 'Private uploaded utility bill'
  try {
    const parsed = new URL(value)
    return `${parsed.hostname.replace(/^www\./, '')} · Official source`
  } catch {
    return 'Private server source'
  }
}

function pricingModelLabel(value: string): string {
  return {
    flat: 'Flat',
    time_of_use: 'Time of use',
    tiered: 'Billing-cycle tiered',
    time_of_use_tiered: 'Time of use with tiers',
  }[value] ?? value.replaceAll('_', ' ')
}

function friendlySourceType(value: string): string {
  if (value.includes('utility_bill')) return 'Reviewed bill evidence'
  if (value.includes('tariff_pdf')) return 'Official tariff PDF'
  if (value.includes('tou_html')) return 'Official residential rate page'
  return 'Managed rate evidence'
}
