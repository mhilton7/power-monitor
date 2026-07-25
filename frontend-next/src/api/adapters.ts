import type {
  AdvancedHealthSummary,
  AlertSummary,
  BackupSummary,
  BillImportDetail,
  BillSummary,
  BillingCycleSummary,
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
  return records(value, 'sensors').map((source) => {
    const state = stringValue(source.status, 'unknown')
    return {
      id: stringValue(source.id),
      name: stringValue(source.name, 'Unnamed sensor'),
      state,
      online: onlineStates.has(state),
      currentPowerW: optionalString(source.current_watts),
      lastSeenAt: optionalString(source.last_seen_at),
      storageHealthy: typeof source.sd_ok === 'boolean' ? source.sd_ok : undefined,
      wifiDbm: typeof source.rssi_dbm === 'number' ? source.rssi_dbm : undefined,
      firmware: optionalString(source.firmware_version),
      monitoredCircuit: stringValue(source.circuit_name, 'Whole home'),
      backlog: numberValue(source.backlog),
      ctRatingAmps: stringValue(source.ct_rating_amps, '100'),
      measurementRole: stringValue(source.measurement_role, 'submeter'),
    }
  })
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

export function adaptBillingCycle(value: unknown): BillingCycleSummary {
  const source = record(value, 'billing cycle')
  const cycle = source.cycle ? record(source.cycle, 'cycle') : {}
  const currentTier = source.current_tier ? record(source.current_tier, 'current tier') : {}
  return {
    available: booleanValue(source.available),
    startsAt: optionalString(cycle.starts_at),
    endsAt: optionalString(cycle.ends_at),
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
    currentPowerW: stringValue(fleet.current_load_w, '0'),
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
    recentPeakW: stringValue(fleet.recent_peak_w, '0'),
    latestDataAt: optionalString(fleet.latest_heartbeat_at),
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

export function adaptBillDetail(value: unknown): BillImportDetail {
  const source = record(value, 'bill')
  const cycle = source.billing_cycle ? record(source.billing_cycle, 'bill cycle') : {}
  const extractedFields = objectList(source.fields)
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
    blockingWarnings: stringList(source.blocking_warnings),
    revision: numberValue(source.revision),
    normalized: source.normalized ? record(source.normalized, 'normalized bill') : {},
    fields: extractedFields.map((field) => ({
      id: stringValue(field.id),
      path: stringValue(field.field_key),
      label: stringValue(field.field_key).replaceAll('_', ' '),
      value: optionalString(field.effective_value),
      confidence: optionalString(field.confidence),
      sourcePage: typeof field.page_number === 'number' ? field.page_number : undefined,
      status: optionalString(field.review_state),
    })),
    conflicts: objectList(source.conflicts).map((conflict) => ({
      id: stringValue(conflict.id),
      path: stringValue(conflict.field_key),
      message: `Bill: ${stringValue(conflict.extracted_value, 'unknown')} · Current: ${stringValue(conflict.configured_value, 'unknown')}`,
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
  return records(value, 'backups').map((backup) => ({
    id: stringValue(backup.id),
    createdAt: stringValue(backup.started_at, stringValue(backup.created_at)),
    status: stringValue(backup.status, 'unknown'),
    verifiedAt: optionalString(backup.verified_at),
    sizeBytes: typeof backup.size_bytes === 'number' ? backup.size_bytes : undefined,
    encrypted: booleanValue(backup.encrypted),
  }))
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
