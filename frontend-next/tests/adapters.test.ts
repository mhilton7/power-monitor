import { describe, expect, it } from 'vitest'
import {
  adaptAlerts,
  adaptBillDetail,
  adaptConfigurationStatus,
  adaptCurrentRateAssignment,
  adaptElectricServices,
  adaptFamily,
  adaptFamilyRoles,
  adaptHistory,
  adaptHomeSummary,
  adaptNotificationHistory,
  adaptNotificationSuppressions,
  adaptPermissions,
  adaptRateAssignments,
  adaptRateAdjustments,
  adaptRateAssignmentResult,
  adaptRateEvidence,
  adaptRatePlanDependencies,
  adaptRateSourceCheckRun,
  adaptRateSourceCheckRuns,
  adaptRateSources,
  adaptRateVersions,
  adaptSensors,
  adaptSensorStorage,
  adaptSystemHealth,
  adaptTestMode,
  adaptTestModeHistory,
  adaptTestModeSensors,
  resolveSingleHome,
} from '../src/api/adapters'

describe('typed homeowner adapters', () => {
  it('normalizes bounded storage evidence without inventing missing values', () => {
    const storage = adaptSensorStorage({
      schema_version: 'sensor-storage/1.0',
      device_id: 'sensor-1',
      device_name: 'Outdoor-AC',
      observed_at: '2026-07-31T20:00:00Z',
      available: true,
      healthy: true,
      status: 'warning',
      details: {
        pressure_state: 'warning',
        capacity_bytes: 34359738368,
        free_bytes: 3221225472,
        free_percent: 9,
        server_ack_sequence: 120,
        newest_stored_sequence: 150,
        server_event_ack_sequence: 42,
        unsynchronized_count: 30,
        eligible_reclaimable_bytes: 536870912,
        blocked_unacknowledged_bytes: 134217728,
        estimated_days_remaining: 45,
        event_segment_count: 4,
        temporary_artifact_count: 1,
        cleanup_recovery_required: true,
      },
      desired_policy: {
        retention_mode: 'continuous_protected', retention_days: 730,
        minimum_local_history_days: 30, storage_notice_percent: 20,
        storage_warning_percent: 10, storage_critical_percent: 5,
        storage_emergency_percent: 2, storage_emergency_reserve_bytes: 536870912,
        storage_cleanup_target_percent: 10, storage_cleanup_target_bytes: 1073741824,
        event_retention_days: 730,
      },
      effective_policy: {},
      desired_config_version: 8,
      effective_config_version: 7,
      policy_pending: true,
    })
    expect(storage).toMatchObject({
      deviceName: 'Outdoor-AC', pressureState: 'warning', freePercent: 9,
      serverAckSequence: 120, newestStoredSequence: 150,
      unsynchronizedCount: 30, estimatedDaysRemaining: 45, policyPending: true,
      eventSegmentCount: 4, temporaryArtifactCount: 1, cleanupRecoveryRequired: true,
      desiredPolicy: { retentionMode: 'continuous_protected', retentionDays: 730 },
    })
    expect(storage.lastCleanupBytes).toBeUndefined()
    expect(() => adaptSensorStorage({ schema_version: 'future/2.0' })).toThrow()
  })

  it('validates detailed notification, suppression, and history contracts', () => {
    const alerts = adaptAlerts([{
      id: 'alert-1',
      code: 'heartbeat_stale',
      kind: 'operational_alert',
      category: 'connectivity',
      severity: 'error',
      state: 'open',
      title: 'Indoor-AC stopped reporting',
      summary: 'No signed heartbeat arrived within 60 seconds.',
      affected_resource: { type: 'sensor', id: 'sensor-1', name: 'Indoor-AC' },
      first_seen_at: '2026-07-31T16:00:00Z',
      last_seen_at: '2026-07-31T16:01:32Z',
      occurrence_count: 3,
      duration_seconds: 92,
      observed: { label: 'Last signed heartbeat', value: '92', unit: 'seconds' },
      expected: { label: 'Heartbeat interval', operator: 'within', value: '60', unit: 'seconds' },
      cause: { code: 'heartbeat_stale', explanation: 'The signed heartbeat window expired.' },
      evidence: [{ label: 'Last known power', value: '842.6 W', status: 'warning' }],
      impact: 'Live values may be stale.',
      remediation: {
        summary: 'Check sensor power and network access.',
        steps: ['Confirm the sensor has power.'],
        automatic_recovery: 'The sensor retries after connectivity returns.',
        action: { label: 'Open sensor details', target: '/settings/sensors', required_permissions: ['devices.view'] },
      },
      delivery: { attempted: true, channel_name: 'Home email', last_outcome: 'retry_scheduled', safe_error_code: 'smtp_starttls_failed', safe_error_summary: 'STARTTLS negotiation failed' },
      suppression: { dismissible: false, permanently_suppressible: false, currently_suppressed: false, allowed_scopes: [] },
    }])
    expect(alerts[0]).toMatchObject({
      title: 'Indoor-AC stopped reporting',
      affectedResource: { type: 'sensor', name: 'Indoor-AC' },
      occurrenceCount: 3,
      observed: { value: '92', unit: 'seconds' },
      expected: { value: '60', unit: 'seconds' },
      delivery: { safeErrorCode: 'smtp_starttls_failed' },
      suppression: { permanentlySuppressible: false },
    })

    expect(() => adaptAlerts([{ ...alerts[0], kind: 'mystery' }])).toThrow()
    expect(adaptNotificationSuppressions([{
      id: 'suppression-1', suppression_key: 'recommendation.smtp_not_configured',
      category: 'delivery', scope_type: 'home', scope_name: 'Home', created_by: 'Owner',
      created_at: '2026-07-31T16:00:00Z', source_notification_id: 'recommendation:1',
      active: true, revision: 1,
    }])[0]).toMatchObject({ scopeType: 'home', active: true })
    expect(adaptNotificationHistory({ total: 1, items: [{
      id: 'event-1', notification_id: 'alert-1', event_type: 'acknowledged',
      occurred_at: '2026-07-31T16:02:00Z', category: 'connectivity', severity: 'error',
    }] })).toMatchObject({ total: 1, items: [{ eventType: 'acknowledged' }] })
  })

  it('keeps live sensor measurements consistent and distinguishes zero from missing', () => {
    const sensors = adaptSensors([{
      id: 'sensor-1',
      name: 'Indoor-AC',
      status: 'online_synchronized',
      current_watts: '0.8',
      voltage_volts: '120.4',
      current_amps: '0.01',
      frequency_hz: '60.0',
      power_factor: '0.83',
      latest_measurement_at: '2026-07-29T21:55:00Z',
      measurement_received_at: '2026-07-29T21:55:02Z',
      last_seen_at: '2026-07-29T21:55:02Z',
      measurement_freshness: 'live',
      heartbeat_received_at: '2026-07-29T21:55:02Z',
      heartbeat_age_seconds: 1,
      heartbeat_freshness: 'online',
      offline_after_seconds: 30,
      previous_outage_reason: 'Sensor previously deferred synchronization because internal heap was fragmented.',
      measurement_source: 'heartbeat_live',
      measurement_invalid_metrics: [],
      firmware_ota: {
        state: 'ready', supported: true, protocol_version: 2,
        authentication_mode: 'existing_device_hmac', rollback_supported: true,
        partition_size_bytes: 6291456,
      },
    }])
    const live = adaptHomeSummary({
      current_load_w: '0.8',
      reporting_devices: 1,
      has_live_data: true,
      latest_data_at: '2026-07-29T21:55:00Z',
      latest_received_at: '2026-07-29T21:55:02Z',
      server_now: '2026-07-29T21:55:03Z',
    }, sensors)

    expect(sensors[0]).toMatchObject({
      name: 'Indoor-AC',
      currentPowerW: '0.8',
      voltageVolts: '120.4',
      currentAmps: '0.01',
      frequencyHz: '60.0',
      powerFactor: '0.83',
      measurementFreshness: 'live',
      measurementSource: 'heartbeat_live',
      heartbeatFreshness: 'online',
      online: true,
      previousOutageReason: 'Sensor previously deferred synchronization because internal heap was fragmented.',
      firmwareOta: {
        state: 'ready', supported: true, protocolVersion: 2,
        authenticationMode: 'existing_device_hmac', rollbackSupported: true,
        partitionSizeBytes: 6291456,
      },
    })
    const reconciling = adaptSensors([{
      id: 'sensor-2',
      name: 'Outdoor-AC',
      status: 'online_storage_reconciling',
      measurement_freshness: 'waiting',
      heartbeat_freshness: 'online',
      offline_after_seconds: 30,
    }])[0]
    expect(reconciling).toMatchObject({
      online: true,
      deviceStatus: 'online_storage_reconciling',
      measurementFreshness: 'waiting',
    })
    const staleStoredStatus = adaptSensors([{
      id: 'sensor-3',
      name: 'Stale stored status',
      status: 'online_synchronized',
      measurement_freshness: 'offline',
      heartbeat_freshness: 'offline',
      heartbeat_age_seconds: 31,
      offline_after_seconds: 30,
    }])[0]
    expect(staleStoredStatus).toMatchObject({
      deviceStatus: 'online_synchronized',
      heartbeatFreshness: 'offline',
      measurementFreshness: 'offline',
      online: false,
    })
    expect(live).toMatchObject({
      currentPowerW: '0.8',
      reportingSensors: 1,
      hasLiveData: true,
      latestDataAt: '2026-07-29T21:55:00Z',
      latestReceivedAt: '2026-07-29T21:55:02Z',
      serverNow: '2026-07-29T21:55:03Z',
    })

    const missing = adaptHomeSummary({
      current_load_w: null,
      reporting_devices: 0,
      has_live_data: false,
    }, sensors)
    const legitimateZero = adaptHomeSummary({
      current_load_w: '0',
      reporting_devices: 1,
      has_live_data: true,
    }, sensors)
    expect(missing.currentPowerW).toBeUndefined()
    expect(legitimateZero.currentPowerW).toBe('0')
  })

  it('validates the authoritative current-assignment context', () => {
    expect(adaptCurrentRateAssignment({
      schema_version: 'current-rate-assignment/1.0',
      home_id: 'home-1',
      electric_service_id: 'service-1',
      service_revision: 4,
      assignment: {
        assignment_id: 'assignment-1',
        assignment_revision: 2,
        plan_id: 'plan-1',
        plan_name: 'Domestic',
        version_id: 'version-1',
        version: 3,
        pricing_model: 'tiered',
        effective_from: '2026-07-25T12:00:00Z',
        effective_to: null,
        state: 'current',
      },
    }).assignment).toMatchObject({
      assignmentId: 'assignment-1',
      assignmentRevision: 2,
      planName: 'Domestic',
      version: 3,
      state: 'current',
    })
  })

  it('does not dereference a missing current plan', () => {
    expect(adaptElectricServices([{
      id: 'service-1',
      site_id: 'home-1',
      name: 'Home electric service',
      status: 'active',
      timezone: 'America/Los_Angeles',
      currency: 'USD',
      billing_cycle_start_day: 1,
      current_plan: null,
    }])[0]).toMatchObject({ id: 'service-1', currentPlan: undefined })
  })

  it('uses the canonical rate context and validates assignment results', () => {
    expect(adaptElectricServices([{
      id: 'service-1',
      site_id: 'home-1',
      name: 'Home electric service',
      status: 'active',
      timezone: 'America/Los_Angeles',
      currency: 'USD',
      billing_cycle_start_day: 1,
      revision: 4,
      rate_context: {
        state: 'rate_configured_effective',
        current_plan: 'TOU-D',
        plan_code: 'TOU-D-4-9PM',
        rate_version_id: 'version-2',
        current_version: 2,
        current_assignment_id: 'assignment-2',
        current_assignment_revision: 1,
        current_period: 'Off Peak',
        current_price_per_kwh: '0.34',
      },
    }])[0]).toMatchObject({
      currentPlan: 'TOU-D',
      rateVersionId: 'version-2',
      currentVersion: 2,
      currentAssignmentId: 'assignment-2',
      currentAssignmentRevision: 1,
      currentPeriod: 'Off Peak',
      currentRate: '0.34',
    })
    expect(adaptRateAssignmentResult({
      schema_version: 'rate-assignment-result/1.0',
      assignment_id: 'assignment-2',
      electric_service_id: 'service-1',
      plan_id: 'plan-2',
      version_id: 'version-2',
      version: 2,
      effective_from: '2026-07-25T12:00:00Z',
      effective_to: null,
      state: 'current',
      replaced_assignment_id: 'assignment-1',
      recalculation_job_id: 'cost-job-1',
      warnings: [],
      service_revision: 4,
      idempotent: false,
    })).toMatchObject({
      assignmentId: 'assignment-2',
      electricServiceId: 'service-1',
      versionId: 'version-2',
      state: 'current',
      serviceRevision: 4,
    })
  })

  it('adapts actionable configuration issues without guessing status in pages', () => {
    expect(adaptConfigurationStatus({
      schema_version: 'configuration-status/1.0',
      home_id: 'home-1',
      electric_service_id: 'service-1',
      state: 'setup_needed',
      label: 'Setup needed',
      summary: '1 blocking and 0 advisory issues.',
      generated_at: '2026-07-25T12:00:00Z',
      issues: [{
        id: 'rate-assignment.missing',
        category: 'rate_plan',
        state: 'setup_needed',
        title: 'Choose a current rate plan',
        what_is_wrong: 'No plan is effective now.',
        why_it_matters: 'Costs are unavailable.',
        how_to_fix: 'Choose a published version.',
        blocking: true,
        action: {
          id: 'rate_assignment.make_current',
          label: 'Choose current plan',
          target: '/billing?advanced=rates&tab=versions',
        },
      }],
    })).toMatchObject({
      state: 'setup_needed',
      issues: [{
        id: 'rate-assignment.missing',
        blocking: true,
        action: { target: '/billing?advanced=rates&tab=versions' },
      }],
    })
  })

  it('keeps exact history decimals as strings and explicit gaps', () => {
    const history = adaptHistory({
      scope: { display_name: 'Whole Home' },
      summary: {
        energy_kwh: '1.234567',
        energy_cost: '0.456789',
        coverage_percent: '50.0',
        contributing_sensor_count: 1,
      },
      combined: [{
        interval_start_utc: '2026-07-24T00:00:00Z',
        interval_end_utc: '2026-07-24T00:15:00Z',
        energy_kwh: '1.234567',
        energy_cost: '0.456789',
        coverage_percent: '50.0',
        contributing_sensor_count: 1,
        included_sensor_count: 2,
      }],
      warnings: [{ message: 'Partial coverage' }],
    })

    expect(history.energyKwh).toBe('1.234567')
    expect(history.cost).toBe('0.456789')
    expect(history.points[0]).toMatchObject({ energyKwh: '1.234567', cost: '0.456789', missing: true })
  })

  it('preserves user lifecycle and actual custom role identifiers', () => {
    expect(adaptFamily({ users: [{
      id: 'user-1',
      display_name: 'Alex',
      email: 'alex@example.test',
      roles: ['custom_home_auditor'],
      status: 'disabled',
      active_session_count: 0,
      access_revision: 3,
    }] })[0]).toMatchObject({
      role: 'Viewer',
      roleIds: ['custom_home_auditor'],
      status: 'disabled',
      revision: 3,
    })
  })

  it('validates custom-role revisions and the permission catalog', () => {
    expect(adaptFamilyRoles({ roles: [{
      id: 'custom_home_auditor',
      display_name: 'Home auditor',
      description: 'Reviews home history',
      built_in: false,
      archived: false,
      revision: 4,
      permissions: ['history.view', 'history.export'],
      assigned_user_count: 2,
    }] })[0]).toMatchObject({
      id: 'custom_home_auditor',
      revision: 4,
      permissions: ['history.view', 'history.export'],
      assignedUserCount: 2,
    })
    expect(adaptPermissions({ permissions: [{
      code: 'history.export',
      group: 'Dashboard and data',
      label: 'Export history',
      description: 'Export permitted historical readings.',
      high_risk: false,
    }] })[0]).toMatchObject({ code: 'history.export', highRisk: false })
  })

  it('rejects malformed parent payloads instead of leaking undefined errors', () => {
    expect(() => adaptBillDetail(undefined)).toThrow(/bill/i)
    expect(() => resolveSingleHome({ homes: 'not-an-array' })).toThrow(/homes/i)
  })

  it('validates typed System Health components and release compatibility', () => {
    const health = adaptSystemHealth({
      schema_version: 'system-health/1.0',
      status: 'degraded',
      checked_at: '2026-07-26T20:00:00Z',
      components: [{
        key: 'worker',
        label: 'Worker',
        status: 'degraded',
        summary: 'The worker loop is stale.',
        checked_at: '2026-07-26T20:00:00Z',
        last_success_at: '2026-07-26T19:58:00Z',
        latency_ms: '4.25',
        details: { reported_status: 'running' },
        remediation: { label: 'Review worker', route: '/settings/advanced/logs' },
        can_retry: true,
      }],
      versions: {
        backend: '1.0.0',
        frontend: '1.0.0',
        compatibility: 'compatible',
      },
      recent_events: [{
        occurred_at: '2026-07-26T20:00:00Z',
        component: 'worker',
        status: 'degraded',
        summary: 'The worker loop is stale.',
      }],
    })
    expect(health).toMatchObject({
      schemaVersion: 'system-health/1.0',
      status: 'degraded',
      components: [{
        key: 'worker',
        status: 'degraded',
        latencyMs: 4.25,
        remediation: { route: '/settings/advanced/logs' },
      }],
    })
    expect(() => adaptSystemHealth({
      schema_version: 'system-health/0.9',
      status: 'healthy',
      components: [],
      versions: {},
    })).toThrow(/incompatible/i)
    for (const status of ['healthy', 'degraded', 'unhealthy'] as const) {
      expect(adaptSystemHealth({
        schema_version: 'system-health/1.0',
        status,
        checked_at: '2026-07-26T20:00:00Z',
        components: [{
          key: 'api',
          label: 'API',
          status,
          summary: `API is ${status}.`,
          checked_at: '2026-07-26T20:00:00Z',
        }],
        versions: {
          backend: '1.0.0',
          frontend: '1.0.0',
          compatibility: 'compatible',
        },
        recent_events: [],
      })).toMatchObject({
        status,
        components: [{ key: 'api', status }],
      })
    }
  })

  it('rejects unclassified synthetic data and adapts isolated Test Mode values', () => {
    const state = adaptTestMode({
      enabled: true,
      session_id: 'session-1',
      remaining_seconds: 300,
      sensor_count: 2,
      online_sensors: 1,
      offline_sensors: 1,
      load_profile: 'evening_peak',
      sample_interval_seconds: 5,
      cost_preview_enabled: true,
      current_power_w: '2200.5',
      total_energy_kwh: '0.0125',
      source_type: 'simulated',
      environment: 'test_mode',
      isolation: {
        real_readings: true,
        bills_and_finalized_costs: true,
        exports_and_backups: true,
        alerts: true,
        credentials_and_firmware: true,
      },
      cost_preview: {
        enabled: true,
        available: true,
        energy_kwh: '0.0125',
        estimated_energy_cost: '0.0043',
        currency: 'USD',
        disclosure: 'Temporary only.',
      },
    })
    expect(state).toMatchObject({
      enabled: true,
      sensorCount: 2,
      currentPowerW: 2200.5,
      totalEnergyKwh: 0.0125,
      sourceType: 'simulated',
      environment: 'test_mode',
      costPreview: { estimatedEnergyCost: 0.0043 },
    })
    expect(adaptTestModeSensors([{
      id: 'test-1',
      name: 'Test Sensor 1',
      index: 1,
      online: true,
      current_power_w: '500',
      energy_kwh: '0.01',
      source_type: 'simulated',
      environment: 'test_mode',
    }])).toHaveLength(1)
    expect(adaptTestModeHistory([{
      recorded_at: '2026-07-26T20:00:00Z',
      sensor_id: 'test-1',
      sensor_name: 'Test Sensor 1',
      online: true,
      power_w: '500',
      interval_energy_kwh: '0.001',
      source_type: 'simulated',
      environment: 'test_mode',
    }])[0]).toMatchObject({ powerW: 500, sourceType: 'simulated' })
    expect(() => adaptTestMode({ ...testModePayload(), source_type: 'real' })).toThrow(/unsafe/i)
  })

  it('adapts the canonical normalized bill instead of rendering missing fields as values', () => {
    const bill = adaptBillDetail({
      id: 'bill-1',
      status: 'review_required',
      created_at: '2026-07-25T12:00:00Z',
      revision: 1,
      page_count: 1,
      extraction_method: 'text',
      content_sha256: 'a'.repeat(64),
      cycle_draft: {
        starts_at: '2026-06-22T07:00:00Z',
        ends_at: '2026-07-22T07:00:00Z',
        total_usage_kwh: '951',
        full_bill_total: '354.15',
      },
      normalized_artifact: {
        schema_version: 'normalized-utility-bill/1.0',
        parser_id: 'sce_residential_bill_v1',
        parser_version: '1.1.0',
        artifact: {
          artifact_id: 'artifact-1',
          display_filename: 'sce-bill.pdf',
          sha256: 'a'.repeat(64),
          mime_type: 'application/pdf',
          byte_size: 4264,
          page_count: 1,
          extraction_method: 'text',
          imported_at: '2026-07-25T12:00:00Z',
        },
        utility: {
          name: 'Southern California Edison',
          document_type: 'residential_electric_bill',
          rate_plan_code: 'DOMESTIC',
        },
        billing_cycle: { total_usage_kwh: '951', full_bill_total: '354.15' },
        plan_candidate: {
          plan_code: 'DOMESTIC',
          threshold_interpretation: 'fixed_cycle_threshold',
        },
        line_items: [{ label: 'State tax', amount: '0.29' }],
        evidence: [{
          field: 'total_usage_kwh',
          output_kind: 'billing_cycle',
          value: '951',
          confidence: 'arithmetic_confirmed',
          source_page: 1,
          parser_version: '1.1.0',
        }],
        validation: { valid: true },
        warnings: [],
        missing_fields: [{
          field: 'winter_rates',
          output_kind: 'rate_plan',
          value: null,
          state: 'not_found_on_bill',
          required: false,
          reason: 'Only summer rates are present.',
        }],
        ignored_sections: [],
        processing_status: 'review_required',
      },
      fields: [{
        id: 'present-field',
        output_kind: 'billing_cycle',
        field_key: 'total_usage_kwh',
        effective_value: '951',
        confidence: 'high',
        page_number: 1,
      }, {
        id: 'missing-field',
        output_kind: 'rate_plan',
        field_key: 'winter_rates',
        effective_value: null,
        confidence: 'missing',
      }],
      conflicts: [{
        id: 'pricing-conflict',
        field_key: 'pricing_model',
        extracted_value: 'flat',
        configured_value: 'time_of_use',
      }],
      blocking_warnings: [],
    })

    expect(bill).toMatchObject({
      displayFilename: 'sce-bill.pdf',
      utilityName: 'Southern California Edison',
      usageKwh: '951',
      total: '354.15',
      thresholdInterpretation: 'fixed_cycle_threshold',
    })
    expect(bill.fields).toHaveLength(1)
    expect(bill.fields[0]).toMatchObject({ path: 'total_usage_kwh', value: '951' })
    expect(bill.missingFields).toEqual([expect.objectContaining({
      path: 'winter_rates',
      required: false,
    })])
    expect(bill.conflicts[0]?.message).toBe(
      'Uploaded bill: Flat · current plan: Time of use. Confirming uses the bill value for the new draft and preserves existing history.',
    )
    expect(JSON.stringify(bill)).not.toContain('Unknown')
    expect(() => adaptBillDetail({
      ...billPayloadWithConfidence('administrator_confirmed'),
    })).toThrow(/confidence/i)
  })

  it('normalizes rate lifecycle payloads while preserving exact evidence and dates', () => {
    expect(adaptRateVersions([{
      id: 'version-1',
      version: 3,
      status: 'published',
      publication_status: 'published',
      assignment_status: 'current',
      display_status: 'current',
      effective_from: '2026-07-24',
      pricing_model: 'time_of_use_tiered',
      integrity_sha256: 'abc123',
      immutable_after_use: false,
      parent_version_id: 'version-0',
      lifecycle_revision: 4,
      assignments: [{
        id: 'assignment-1',
        utility_account_id: 'service-1',
        rate_version_id: 'version-1',
        effective_from: '2026-07-24T00:00:00Z',
        state: 'current',
        revision: 2,
      }],
    }])[0]).toMatchObject({
      id: 'version-1',
      version: 3,
      pricingModel: 'time_of_use_tiered',
      integritySha256: 'abc123',
      publicationStatus: 'published',
      assignmentStatus: 'current',
      displayStatus: 'current',
      parentVersionId: 'version-0',
      lifecycleRevision: 4,
      assignments: [expect.objectContaining({ id: 'assignment-1', state: 'current' })],
    })
    expect(adaptRateAssignments([{
      id: 'assignment-1',
      utility_account_id: 'service-1',
      rate_version_id: 'version-1',
      effective_from: '2026-07-24T00:00:00Z',
    }])[0]).toMatchObject({ serviceId: 'service-1', versionId: 'version-1' })
    expect(adaptRateEvidence({ source_evidence: [{
      artifact_id: 'artifact-1',
      sha256: 'def456',
      parser_id: 'utility_bill_pdf_v1',
      relationship: 'supporting',
    }] })[0]).toMatchObject({ id: 'artifact-1', checksum: 'def456', displaySource: 'Reviewed bill evidence' })
    expect(adaptRatePlanDependencies({
      dependency_token: 'a'.repeat(64),
      active_assignments: [{ id: 'assignment-1' }],
      future_assignments: [],
      active_account_pointers: [{ utility_account_id: 'service-1' }],
      historical_assignment_count: 2,
      historical_calculation_count: 4,
      source_evidence_count: 3,
      bill_import_count: 1,
      permanent_draft_deletion_eligible: false,
      removal_blocked: true,
    })).toMatchObject({
      dependencyToken: 'a'.repeat(64),
      historicalAssignmentCount: 2,
      sourceEvidenceCount: 3,
      removalBlocked: true,
    })
    expect(() => adaptRatePlanDependencies({ dependency_token: 'stale' })).toThrow(/concurrency token/i)
  })

  it('keeps raw managed-source identifiers behind a human-readable adapter boundary', () => {
    expect(adaptRateSources({ sources: [{
      id: 'source-1',
      name: 'Private bill source',
      url: 'urn:power-monitor:utility-bill:service-1',
      parser_id: 'utility_bill_pdf_v1',
      enabled: false,
    }] })[0]).toMatchObject({
      displayOrigin: 'Private uploaded utility bill',
      sourceType: 'Reviewed bill evidence',
      technicalUrl: 'urn:power-monitor:utility-bill:service-1',
    })
  })

  it('adapts observable source-check progress, results, history, and adjustments', () => {
    const runPayload = {
      id: 'job-1',
      status: 'succeeded',
      trigger_type: 'manual',
      requested_at: '2026-07-25T12:00:00Z',
      started_at: '2026-07-25T12:00:01Z',
      completed_at: '2026-07-25T12:00:03Z',
      progress: { completed: 1, total: 1, current_source_id: null },
      sources_attempted: 1,
      successes: 1,
      failures: 0,
      candidates: 2,
      archived_evidence: 1,
      items: [{
        id: 'check-1',
        source_id: 'source-1',
        source_name: 'SCE source',
        job_id: 'job-1',
        outcome: 'succeeded',
        checked_at: '2026-07-25T12:00:01Z',
        finished_at: '2026-07-25T12:00:03Z',
        candidate_count: 2,
        artifact_count: 1,
      }],
    }
    expect(adaptRateSourceCheckRun(runPayload)).toMatchObject({
      id: 'job-1',
      status: 'succeeded',
      progress: { completed: 1, total: 1 },
      candidates: 2,
      archivedEvidence: 1,
      items: [expect.objectContaining({
        sourceId: 'source-1',
        sourceName: 'SCE source',
        candidateCount: 2,
      })],
    })
    expect(adaptRateSourceCheckRuns({ runs: [runPayload] })).toHaveLength(1)
    expect(adaptRateAdjustments([{
      id: 'adjustment-1',
      component: 'custom_per_kwh',
      value: '0.01250000',
      unit: 'per_kwh',
      provenance: 'Reviewed tariff',
      reason: 'Administrator-approved local adjustment',
      evidence_reference: 'tariff-page-2',
      effective_from: '2026-07-25T12:00:00Z',
      enabled: true,
      status: 'active',
      revision: 3,
    }])[0]).toMatchObject({
      id: 'adjustment-1',
      value: '0.01250000',
      reason: 'Administrator-approved local adjustment',
      evidenceReference: 'tariff-page-2',
      revision: 3,
    })
  })
})

function billPayloadWithConfidence(confidence: string) {
  return {
    id: 'bill-invalid-confidence',
    status: 'review_required',
    created_at: '2026-07-25T12:00:00Z',
    revision: 1,
    page_count: 1,
    extraction_method: 'text',
    cycle_draft: {},
    normalized_artifact: {
      schema_version: 'normalized-utility-bill/1.0',
      parser_id: 'sce_residential_bill_v1',
      parser_version: '1.1.0',
      artifact: {
        artifact_id: 'artifact-1',
        display_filename: 'bill.pdf',
        sha256: 'a'.repeat(64),
        mime_type: 'application/pdf',
        page_count: 1,
        extraction_method: 'text',
        imported_at: '2026-07-25T12:00:00Z',
      },
      utility: {},
      billing_cycle: {},
      plan_candidate: {},
      line_items: [],
      evidence: [],
      validation: {},
      warnings: [],
      missing_fields: [],
      ignored_sections: [],
      processing_status: 'review_required',
    },
    fields: [{
      id: 'field-1',
      output_kind: 'billing_cycle',
      field_key: 'total_usage_kwh',
      effective_value: '951',
      confidence,
    }],
    conflicts: [],
    blocking_warnings: [],
  }
}

function testModePayload() {
  return {
    enabled: false,
    remaining_seconds: 0,
    sensor_count: 0,
    online_sensors: 0,
    offline_sensors: 0,
    sample_interval_seconds: 5,
    cost_preview_enabled: false,
    current_power_w: '0',
    total_energy_kwh: '0',
    source_type: 'simulated',
    environment: 'test_mode',
    isolation: {},
  }
}
