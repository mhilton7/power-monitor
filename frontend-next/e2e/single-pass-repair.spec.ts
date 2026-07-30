import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'
import { newRateDraft } from '../src/features/rates/rateDocument'

const home = {
  id: 'home-1',
  name: 'Upland Home',
  timezone: 'America/Los_Angeles',
  currency: 'USD',
  lifecycle_state: 'active',
  is_default: true,
  revision: 1,
}

const currentRateDocument = {
  ...newRateDraft(home),
  plan_name: 'TOU-D 4 PM to 9 PM',
  plan_code: 'TOU-D-4-9PM',
  effective_from: '2026-07-01',
}

const service = {
  id: 'service-1',
  site_id: home.id,
  nickname: 'Home electric service',
  utility_name: 'SCE',
  generation_provider: 'sce',
  timezone: home.timezone,
  currency: home.currency,
  billing_cycle_start_day: 1,
  cost_scope: 'energy_only',
  status: 'active',
  revision: 1,
  readiness: { rate: 'ready', cost: 'ready', topology_complete: true },
  rate_context: {
    current_plan: 'TOU-D 4 PM to 9 PM',
    current_version: 2,
    current_period: 'Off-Peak',
    current_price_per_kwh: '0.34400000',
    next_period: 'On-Peak',
    next_price_per_kwh: '0.56700000',
    billing_cycle: { starts_at: '2026-07-01T07:00:00Z', ends_at: '2026-08-01T07:00:00Z' },
  },
}

const bill = {
  id: 'bill-1',
  utility_account_id: service.id,
  status: 'extracted',
  extraction_method: 'text',
  created_at: '2026-07-24T23:37:00Z',
  page_count: 6,
  revision: 1,
  cycle_draft: {
    id: 'cycle-draft-1',
    total_usage_kwh: '951.000',
    full_bill_total: '355.00',
    starts_at: '2026-06-01T07:00:00Z',
    ends_at: '2026-07-01T07:00:00Z',
  },
  fields: [
    { id: 'field-1', field_key: 'plan_name', output_kind: 'rate_plan', effective_value: 'DOMESTIC', confidence: 'parser_confirmed', page_number: 1 },
    { id: 'field-2', field_key: 'total_usage_kwh', output_kind: 'billing_cycle', effective_value: '951.000', confidence: 'arithmetic_confirmed', page_number: 1 },
    { id: 'field-3', field_key: 'tier_1_rate', output_kind: 'rate_plan', effective_value: '0.30000000', confidence: 'arithmetic_confirmed', page_number: 3 },
  ],
  conflicts: [],
  blocking_warnings: [],
  normalized_artifact: {
    schema_version: 'normalized-utility-bill/1.0',
    parser_id: 'sce_residential_bill_v1',
    parser_version: '1.0.0',
    artifact: {
      artifact_id: 'artifact-1',
      display_filename: 'sanitized-sce-domestic-bill.pdf',
      sha256: 'a'.repeat(64),
      mime_type: 'application/pdf',
      byte_size: 4264,
      page_count: 6,
      extraction_method: 'text',
      imported_at: '2026-07-24T23:37:00Z',
    },
    utility: {
      name: 'Southern California Edison',
      document_type: 'residential_electric_bill',
      rate_plan_code: 'DOMESTIC',
    },
    billing_cycle: {
      total_usage_kwh: '951.000',
      full_bill_total: '355.00',
      starts_at: '2026-06-01T07:00:00Z',
      ends_at: '2026-07-01T07:00:00Z',
    },
    plan_candidate: {
      plan_name: 'DOMESTIC',
      plan_code: 'DOMESTIC',
      threshold_interpretation: 'fixed_cycle_threshold',
    },
    line_items: [],
    evidence: [
      { field: 'plan_name', output_kind: 'rate_plan', value: 'DOMESTIC', confidence: 'parser_confirmed', source_page: 1, parser_version: '1.0.0' },
      { field: 'total_usage_kwh', output_kind: 'billing_cycle', value: '951.000', confidence: 'arithmetic_confirmed', source_page: 1, parser_version: '1.0.0' },
    ],
    validation: { status: 'pass' },
    warnings: [],
    missing_fields: [
      { field: 'account_suffix', output_kind: 'account', value: null, state: 'not_found_on_bill', required: false, reason: 'The uploaded detail pages did not show an account number.' },
    ],
    ignored_sections: [],
    page_classifications: [],
    processing_status: 'review_required',
  },
}

const ratePlans = {
  plans: [
    {
      id: 'plan-1',
      name: 'TOU-D 4 PM to 9 PM',
      code: 'TOU-D-4-9PM',
      status: 'active',
      lifecycle_revision: 2,
      versions: [{
        id: 'version-1',
        version: 2,
        status: 'published',
        publication_status: 'published',
        assignment_status: 'current',
        display_status: 'current',
        pricing_model: 'time_of_use',
        lifecycle_revision: 1,
        assignments: [{
          id: 'assignment-current',
          utility_account_id: service.id,
          rate_version_id: 'version-1',
          effective_from: '2026-07-01T07:00:00Z',
          effective_to: null,
          state: 'current',
          revision: 1,
        }],
      }],
    },
    {
      id: 'plan-2',
      name: 'Summer adjustment candidate',
      code: 'SUMMER-ADJUST',
      status: 'active',
      lifecycle_revision: 1,
      versions: [{
        id: 'version-2',
        version: 1,
        status: 'published',
        publication_status: 'published',
        assignment_status: 'unassigned',
        display_status: 'published',
        pricing_model: 'time_of_use',
        lifecycle_revision: 1,
        assignments: [],
      }],
    },
  ],
}

interface MockOptions {
  failFirstBillUpload?: boolean
  billingOnly?: boolean
  sourceDelayMs?: number
}

interface ObservedRequests {
  rateDraft?: Record<string, unknown>
  assignment?: Record<string, unknown>
  assignmentRequestCount: number
  billPublished: boolean
  cycleImported: boolean
  draftDeleted: boolean
  retired: boolean
  removed: boolean
  restored: boolean
  sourceAdded: boolean
  sourceCheckStarted: boolean
  sourceCheckRequests: number
  adjustmentCreated: boolean
  adjustmentUpdated: boolean
  adjustmentRemoved: boolean
}

async function mockRepairServer(page: Page, configured = false, options: MockOptions = {}) {
  let billUploadAttempts = 0
  let sourceRunPolls = 0
  let adjustment: Record<string, unknown> | undefined
  const liveMeasurementAt = new Date().toISOString()
  const hasBilling = configured || options.billingOnly === true
  const observed: ObservedRequests = {
    assignmentRequestCount: 0,
    billPublished: false,
    cycleImported: false,
    draftDeleted: false,
    retired: false,
    removed: false,
    restored: false,
    sourceAdded: false,
    sourceCheckStarted: false,
    sourceCheckRequests: 0,
    adjustmentCreated: false,
    adjustmentUpdated: false,
    adjustmentRemoved: false,
  }
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const requestUrl = new URL(request.url())
    const pathname = requestUrl.pathname
    if (pathname === '/api/v1/events/stream') return route.fulfill({ status: 204 })
    if (pathname === '/api/v1/admin/utility-bill-imports' && request.method() === 'POST') {
      billUploadAttempts += 1
      if (options.failFirstBillUpload && billUploadAttempts === 1) {
        return route.fulfill({
          status: 503,
          contentType: 'application/problem+json',
          json: { title: 'Extractor temporarily unavailable', detail: 'Retry the reviewed upload.', status: 503 },
        })
      }
      await new Promise((resolve) => setTimeout(resolve, 250))
      return route.fulfill({ status: 201, json: bill })
    }
    if (pathname === '/api/v1/admin/utility-bill-imports' && request.method() === 'GET') {
      return route.fulfill({ json: observed.cycleImported ? [{ ...bill, status: 'imported', billing_cycle: bill.cycle_draft }] : [] })
    }
    if (pathname === `/api/v1/admin/utility-bill-imports/${bill.id}/review`) return route.fulfill({ json: { ...bill, status: 'reviewed', revision: 2 } })
    if (pathname === `/api/v1/admin/utility-bill-imports/${bill.id}/validate`) return route.fulfill({ json: { validation: { valid: true }, blocking_warnings: [] } })
    if (pathname === `/api/v1/admin/utility-bill-imports/${bill.id}/publish-and-assign`) {
      observed.billPublished = true
      return route.fulfill({ json: { status: 'active' } })
    }
    if (pathname === `/api/v1/admin/utility-bill-imports/${bill.id}/import-billing-cycle`) {
      observed.cycleImported = true
      return route.fulfill({ json: { status: 'imported' } })
    }
    if (pathname === '/api/v1/rates/plans' && request.method() === 'POST') {
      observed.rateDraft = request.postDataJSON() as Record<string, unknown>
      return route.fulfill({ status: 201, json: { plan: { id: 'draft-plan', versions: [{ id: 'draft-version', status: 'draft', version: 1 }] } } })
    }
    if (pathname === '/api/v1/rates/validate-document' && request.method() === 'POST') {
      return route.fulfill({ json: { valid: true, errors: [], warnings: [], integrity_sha256: 'a'.repeat(64) } })
    }
    if (pathname === '/api/v1/rates/preview-cost' && request.method() === 'POST') {
      return route.fulfill({ json: { energy_charge: '125.00000000', blended_energy_rate: '0.25000000', display_total: '125.00' } })
    }
    if (pathname === '/api/v1/rates/versions/draft-version/activate' && request.method() === 'POST') {
      return route.fulfill({
        json: {
          id: 'draft-version',
          status: 'published',
          publication_status: 'published',
          assignment_status: 'unassigned',
          display_status: 'published',
        },
      })
    }
    if (pathname === '/api/v1/rates/assignments/replace' && request.method() === 'POST') {
      observed.assignmentRequestCount += 1
      const assignmentPayload = request.postDataJSON() as Record<string, unknown>
      if (
        assignmentPayload.rate_version_id === 'version-2' &&
        String(assignmentPayload.effective_from).startsWith('2026-08-01')
      ) {
        return route.fulfill({
          status: 409,
          contentType: 'application/problem+json',
          json: {
            title: 'Rate assignment overlaps an existing assignment',
            detail: 'The requested effective range overlaps assignment assignment-current.',
            status: 409,
            conflicting_assignment_id: 'assignment-current',
            allowed_resolution_actions: ['replace_current', 'cancel_scheduled'],
          },
        })
      }
      observed.assignment = assignmentPayload
      const assignedVersion = String(assignmentPayload.rate_version_id)
      return route.fulfill({
        status: 200,
        json: {
          schema_version: 'rate-assignment-result/1.0',
          assignment_id: 'assignment-1',
          electric_service_id: service.id,
          plan_id: assignedVersion === 'version-2' ? 'plan-2' : 'draft-plan',
          version_id: assignedVersion,
          version: assignedVersion === 'version-2' ? 1 : 1,
          effective_from: String(assignmentPayload.effective_from),
          effective_to: null,
          state: 'current',
          effective_now: true,
          replaced_assignment_id: 'assignment-current',
          replaced_assignment_ids: ['assignment-current'],
          recalculation_job_id: 'cost-recalculation-1',
          cost_recalculation: { queued_runs: 1, queued_run_ids: ['cost-recalculation-1'] },
          warnings: [],
          service_revision: 2,
          history_preserved: true,
          idempotent: false,
        },
      })
    }
    if (pathname === '/api/v1/admin/rate-plans/plan-2/dependencies') {
      return route.fulfill({ json: {
        dependency_token: 'b'.repeat(64),
        active_assignments: [],
        future_assignments: [],
        active_account_pointers: [],
        historical_assignment_count: 1,
        historical_calculation_count: 4,
        source_evidence_count: 2,
        bill_import_count: 1,
        permanent_draft_deletion_eligible: false,
        removal_blocked: false,
        preservation: { versions: true, source_evidence: true },
      } })
    }
    if (pathname === '/api/v1/rates/versions/version-2/retire' && request.method() === 'POST') {
      observed.retired = true
      return route.fulfill({ json: { status: 'retired' } })
    }
    if (
      pathname === '/api/v1/rates/plans/plan-1/versions' &&
      request.method() === 'POST'
    ) {
      return route.fulfill({
        status: 201,
        json: {
          id: 'draft-adjustment-version',
          version: 3,
          parent_version_id: 'version-1',
          publication_status: 'draft',
          assignment_status: 'unassigned',
          lifecycle_revision: 1,
        },
      })
    }
    if (pathname === '/api/v1/rates/versions/draft-adjustment-version') {
      return route.fulfill({
        json: {
          document: {
            ...currentRateDocument,
            effective_from: '2026-08-01',
          },
        },
      })
    }
    if (pathname === '/api/v1/rates/versions/version-1') {
      return route.fulfill({ json: { document: currentRateDocument } })
    }
    if (pathname === '/api/v1/rates/plans/plan-1/versions') {
      return route.fulfill({ json: ratePlans.plans[0]?.versions ?? [] })
    }
    if (pathname === '/api/v1/rates/plans/plan-2/versions') {
      return route.fulfill({
        json: [
          ...(ratePlans.plans[1]?.versions ?? []),
          {
            id: 'version-draft-unused',
            version: 2,
            status: 'draft',
            publication_status: 'draft',
            assignment_status: 'unassigned',
            display_status: 'draft',
            pricing_model: 'time_of_use',
            lifecycle_revision: 1,
            assignments: [],
          },
        ],
      })
    }
    if (pathname === '/api/v1/rates/versions/version-draft-unused/dependencies') {
      return route.fulfill({
        json: {
          current_assignment_count: 0,
          future_assignment_count: 0,
          historical_assignment_count: 0,
          historical_calculation_count: 0,
          source_evidence_count: 0,
          delete_draft_eligible: true,
        },
      })
    }
    if (
      pathname === '/api/v1/rates/versions/version-draft-unused/draft' &&
      request.method() === 'DELETE'
    ) {
      observed.draftDeleted = true
      return route.fulfill({ status: 204 })
    }
    if (pathname === '/api/v1/rates/versions/version-2/dependencies') {
      return route.fulfill({
        json: {
          current_assignment_count: 0,
          future_assignment_count: 0,
          historical_assignment_count: 0,
          historical_calculation_count: 0,
          source_evidence_count: 1,
          delete_draft_eligible: false,
        },
      })
    }
    if (pathname === '/api/v1/admin/rate-plans/plan-2/remove' && request.method() === 'POST') {
      observed.removed = true
      return route.fulfill({ json: { plan: { id: 'plan-2', status: 'removed' } } })
    }
    if (pathname === '/api/v1/admin/rate-plans/plan-2/restore' && request.method() === 'POST') {
      observed.restored = true
      observed.removed = false
      return route.fulfill({ json: { plan: { id: 'plan-2', status: 'active' } } })
    }
    if (pathname === '/api/v1/admin/rate-sources' && request.method() === 'POST') {
      observed.sourceAdded = true
      return route.fulfill({ status: 201, json: { id: 'source-3' } })
    }
    if (pathname === '/api/v1/admin/rate-sources/check-now' && request.method() === 'POST') {
      observed.sourceCheckStarted = true
      observed.sourceCheckRequests += 1
      return route.fulfill({
        status: 202,
        json: {
          job_id: 'source-job-1',
          status: 'queued',
          deduplicated: false,
          progress: { completed: 0, total: 1 },
        },
      })
    }
    if (pathname === '/api/v1/admin/rate-sources/check-runs/source-job-1') {
      sourceRunPolls += 1
      if (options.sourceDelayMs) {
        await new Promise((resolve) =>
          setTimeout(resolve, Math.min(options.sourceDelayMs ?? 0, 100)),
        )
      }
      const running = Boolean(options.sourceDelayMs && sourceRunPolls === 1)
      return route.fulfill({
        json: {
          id: 'source-job-1',
          status: running ? 'running' : 'succeeded',
          trigger_type: 'manual',
          requested_at: '2026-07-25T12:00:00Z',
          started_at: '2026-07-25T12:00:01Z',
          completed_at: running ? null : '2026-07-25T12:00:02Z',
          progress: {
            completed: running ? 0 : 1,
            total: 1,
            current_source_id: running ? 'source-1' : null,
          },
          sources_attempted: 1,
          successes: running ? 0 : 1,
          failures: 0,
          candidates: running ? 0 : 1,
          archived_evidence: running ? 0 : 1,
          items: running ? [] : [{
            id: 'source-result-1',
            source_id: 'source-1',
            source_name: 'SCE Residential TOU Page',
            outcome: 'succeeded',
            checked_at: '2026-07-25T12:00:01Z',
            finished_at: '2026-07-25T12:00:02Z',
            http_status: 200,
            candidate_count: 1,
            artifact_count: 1,
          }],
        },
      })
    }
    if (
      pathname === `/api/v1/admin/utility-accounts/${service.id}/adjustments`
      && request.method() === 'GET'
    ) {
      return route.fulfill({ json: adjustment ? [adjustment] : [] })
    }
    if (
      pathname === `/api/v1/admin/utility-accounts/${service.id}/adjustments`
      && request.method() === 'POST'
    ) {
      adjustment = {
        id: 'adjustment-1',
        utility_account_id: service.id,
        ...(request.postDataJSON() as Record<string, unknown>),
        status: 'active',
        revision: 1,
      }
      observed.adjustmentCreated = true
      return route.fulfill({ status: 201, json: adjustment })
    }
    if (
      pathname === `/api/v1/admin/utility-accounts/${service.id}/adjustments/adjustment-1`
      && request.method() === 'PATCH'
    ) {
      adjustment = {
        ...(adjustment ?? {}),
        ...(request.postDataJSON() as Record<string, unknown>),
        revision: 2,
      }
      observed.adjustmentUpdated = true
      return route.fulfill({ json: adjustment })
    }
    if (
      pathname === `/api/v1/admin/utility-accounts/${service.id}/adjustments/adjustment-1`
      && request.method() === 'DELETE'
    ) {
      adjustment = undefined
      observed.adjustmentRemoved = true
      return route.fulfill({ status: 204 })
    }
    if (pathname === '/api/v1/rates/plans' && request.method() === 'GET') {
      const status = requestUrl.searchParams.get('status')
      if (status === 'removed') {
        return route.fulfill({
          json: {
            plans: observed.removed
              ? [{ ...ratePlans.plans[1], status: 'removed', removal_reason: 'Lifecycle test' }]
              : [],
          },
        })
      }
      if (status === 'retired') return route.fulfill({ json: { plans: [] } })
      return route.fulfill({
        json: {
          plans: observed.removed
            ? ratePlans.plans.filter((plan) => plan.id !== 'plan-2')
            : ratePlans.plans,
        },
      })
    }
    const responses: Record<string, unknown> = {
      '/api/v1/auth/session': {
        authenticated: true,
        bootstrap_required: false,
        user: { id: 'owner-1', email: 'owner@example.test', display_name: 'Home Owner', roles: ['admin'], permissions: ['rates.view', 'rates.manage_custom', 'rates.manage_sources', 'rates.check_sources', 'rates.review_candidates', 'rates.assign', 'rates.remove', 'rates.restore', 'adjustments.manage'], all_sites: true, site_ids: [] },
      },
      '/api/v1/sites': [home],
      '/api/v1/devices': configured ? [{
        id: 'sensor-1',
        name: 'Main panel',
        status: 'online_synchronized',
        current_watts: '1420',
        voltage_volts: '120.4',
        current_amps: '11.79',
        frequency_hz: '60.0',
        power_factor: '1.00',
        latest_measurement_at: liveMeasurementAt,
        measurement_received_at: liveMeasurementAt,
        measurement_sequence: 1204,
        measurement_source: 'committed_reading',
        measurement_freshness: 'live',
        last_seen_at: liveMeasurementAt,
        circuit_name: 'Whole home',
        measurement_role: 'full_account',
        ct_rating_amps: '200',
      }] : [],
      '/api/v1/utility-accounts': [service],
      '/api/v1/electric-services/default/current-rate-assignment': {
        schema_version: 'current-rate-assignment/1.0',
        home_id: home.id,
        electric_service_id: service.id,
        service_revision: service.revision,
        assignment: {
          assignment_id: 'assignment-current',
          assignment_revision: 1,
          plan_id: 'plan-1',
          plan_code: 'TOU-D-4-9PM',
          plan_name: 'TOU-D 4 PM to 9 PM',
          version_id: 'version-1',
          version: 2,
          pricing_model: 'time_of_use',
          effective_from: '2026-07-01T07:00:00Z',
          effective_to: null,
          state: 'current',
        },
      },
      '/api/v1/configuration-status': configured ? {
        schema_version: 'configuration-status/1.0',
        home_id: home.id,
        electric_service_id: service.id,
        state: 'ready',
        label: 'Ready',
        summary: 'Configuration is complete.',
        generated_at: '2026-07-25T12:00:00Z',
        issues: [],
      } : {
        schema_version: 'configuration-status/1.0',
        home_id: home.id,
        electric_service_id: service.id,
        state: 'waiting_for_data',
        label: 'Waiting for data',
        summary: '1 blocking and 0 advisory issues.',
        generated_at: '2026-07-25T12:00:00Z',
        issues: [{
          id: 'sensor.missing',
          category: 'sensor',
          state: 'waiting_for_data',
          title: 'Connect a sensor',
          what_is_wrong: 'No active sensor is enrolled for this home.',
          why_it_matters: 'Live power and history require signed sensor readings.',
          how_to_fix: 'Generate an enrollment code and claim the sensor.',
          blocking: true,
          action: {
            id: 'sensor.enroll',
            label: 'Connect sensor',
            target: '/settings/sensors?action=add',
          },
        }],
      },
      [`/api/v1/utility-accounts/${service.id}/tier-status`]: {
        available: hasBilling,
        cycle: { starts_at: '2026-07-01T07:00:00Z', ends_at: '2026-08-01T07:00:00Z', days_remaining: 8 },
        current_period: 'Off-Peak',
        current_rate: '0.34400000',
        usage_kwh: '481.250',
        energy_charge: '165.55',
        projected_bill: '265.20',
        coverage_percent: '98.5',
        projection_confidence: 'high',
        tiers: [],
        warnings: [],
      },
      '/api/v1/fleet/summary': {
        current_load_w: configured ? '1420' : null,
        energy_today_kwh: configured ? '12.450' : '0',
        estimated_cost_today: configured ? '4.28' : '0',
        billing_cycle_energy_kwh: configured ? '481.250' : '0',
        billing_cycle_estimated_cost: configured ? '165.55' : '0',
        projected_bill: configured ? '265.20' : hasBilling ? '0.00' : null,
        reporting_devices: configured ? 1 : 0,
        total_devices: configured ? 1 : 0,
        online_devices: configured ? 1 : 0,
        active_alerts: 0,
        recent_peak_w: configured ? '3850' : null,
        latest_data_at: configured ? liveMeasurementAt : null,
        latest_measurement_at: configured ? liveMeasurementAt : null,
        latest_received_at: configured ? liveMeasurementAt : null,
        latest_heartbeat_at: configured ? liveMeasurementAt : null,
        server_now: liveMeasurementAt,
        has_live_data: configured,
        has_energy_data: configured,
        has_cost_data: configured,
        current_rate_plan: hasBilling ? 'TOU-D 4 PM to 9 PM' : null,
        current_rate: hasBilling ? '0.34400000' : null,
        current_rate_period: hasBilling ? 'Off-Peak' : null,
        next_rate_period: hasBilling ? 'On-Peak' : null,
        next_rate: hasBilling ? '0.56700000' : null,
      },
      '/api/v1/alerts': [],
      '/api/v1/admin/utility-bill-imports': [],
      '/api/v1/rates/assignments/conflicts': {
        conflicts: [],
        requires_explicit_resolution: false,
      },
      '/api/v1/admin/rate-sources/check-runs': [],
      '/api/v1/admin/rate-sources': {
        sources: [
          { id: 'source-1', name: 'SCE Residential TOU Page', url: 'https://www.sce.com/rates', parser_id: 'sce_public_tou_html_v1', enabled: true, last_success_at: '2026-07-24T20:00:00Z' },
          { id: 'source-2', name: 'Private utility bills', url: 'urn:power-monitor:utility-bill:service-1', parser_id: 'utility_bill_pdf_v1', enabled: false },
        ],
      },
      '/api/v1/history/query': {
        scope: { display_name: 'Whole Home' },
        summary: { energy_kwh: configured ? '12.450' : null, energy_cost: configured ? '4.28' : null, coverage_percent: configured ? '60.3444444444444' : '0', contributing_sensor_count: configured ? 1 : 0 },
        combined: configured ? [{
          interval_start_utc: '2026-07-25T11:00:00Z',
          interval_end_utc: '2026-07-25T12:00:00Z',
          local_start: 'Jul 25, 4:00 AM',
          average_power_w: '0.8',
          energy_kwh: '0.0008',
          energy_cost: '0.0003',
          rate_per_kwh: '0.344',
          tou_period: 'Off-Peak',
          coverage_percent: '60.3444444444444',
          contributing_sensor_count: 1,
          included_sensor_count: 1,
          rate_contributions: [],
        }] : [],
        warnings: [],
        rate_versions_used: [],
      },
    }
    const response = responses[pathname]
    if (response !== undefined) return route.fulfill({ json: response })
    return route.fulfill({ json: [] })
  })
  return observed
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('pm-single-home-onboarding-complete', 'true')
  })
})

test('bill importer is visible, keyboard contained, retryable, and URL-backed', async ({ page }) => {
  await mockRepairServer(page)
  await page.goto('/billing')
  await expect(page.getByRole('heading', { name: 'Billing', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Upload electric bill' }).first()).toBeVisible()
  await expect(page).toHaveScreenshot('billing-simple.png', { fullPage: true, animations: 'disabled' })
  const trigger = page.getByRole('button', { name: 'Upload electric bill' }).first()
  await trigger.click()
  await expect(page).toHaveURL(/action=upload/)
  const dialog = page.getByRole('dialog', { name: 'Upload electric bill' })
  await expect(dialog).toBeVisible()
  await expect(page.getByText('Choose your electric bill')).toBeVisible()
  await expect(page.locator('.modal-layer-backdrop')).toBeVisible()
  await expect(page.locator('body')).toHaveCSS('overflow', 'hidden')
  const geometry = await dialog.evaluate((element) => {
    const dialogBox = element.getBoundingClientRect()
    const backdrop = document.querySelector<HTMLElement>('.modal-layer-backdrop')
    const content = element.closest<HTMLElement>('.modal-layer-content')
    return {
      width: dialogBox.width,
      height: dialogBox.height,
      dialogZ: Number(getComputedStyle(content as Element).zIndex),
      backdropZ: Number(getComputedStyle(backdrop as Element).zIndex),
    }
  })
  expect(geometry.width).toBeGreaterThan(300)
  expect(geometry.height).toBeGreaterThan(300)
  expect(geometry.dialogZ).toBeGreaterThan(geometry.backdropZ)
  await expect(dialog.getByRole('button', { name: 'Close bill import' })).toBeFocused()
  await expect(page).toHaveScreenshot('importer-upload.png', { fullPage: true, animations: 'disabled' })

  await page.locator('input[type="file"]').setInputFiles(path.resolve('../backend/tests/fixtures/bills/sanitized-sce-domestic-bill.pdf'))
  await page.getByRole('button', { name: 'Upload and review' }).click()
  await expect(page.getByText('Plan name')).toBeVisible()
  await expect(dialog.getByText('sanitized-sce-domestic-bill.pdf')).toBeVisible()
  await expect(dialog.getByText('Southern California Edison')).toBeVisible()
  await expect(dialog.getByText('6 pages · Text extraction', { exact: false })).toBeVisible()
  await expect(dialog.getByText('Fields not found on this bill (1)')).toBeVisible()
  await expect(dialog.getByText('Unknown', { exact: true })).toHaveCount(0)
  await expect(page).toHaveScreenshot('importer-review.png', { fullPage: true, animations: 'disabled' })
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(page).toHaveURL(/\/billing$/)
  await expect(trigger).toBeFocused()

  await trigger.click()
  await page.reload()
  await expect(dialog).toBeVisible()
  await page.goBack()
  await expect(dialog).toHaveCount(0)
  await expect(page.locator('body')).not.toHaveCSS('overflow', 'hidden')

  await page.goto('/bill-import')
  await expect(page).toHaveURL(/\/billing\?action=upload/)
  await expect(dialog).toBeVisible()
})

test('Billing rate-plan menus close predictably before lifecycle confirmation', async ({ page }) => {
  await mockRepairServer(page, true)
  await page.goto('/billing')
  const trigger = page.getByRole('button', { name: 'Rate plan actions' })

  await trigger.scrollIntoViewIfNeeded()
  await trigger.click()
  await expect(page.getByRole('menuitem', { name: 'Remove plan' })).toBeVisible()
  await page.getByRole('heading', { name: 'Billing', exact: true }).click()
  await expect(page.getByRole('menuitem', { name: 'Remove plan' })).toHaveCount(0)

  await trigger.click()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('menuitem', { name: 'Remove plan' })).toHaveCount(0)
  await expect(trigger).toBeFocused()

  await trigger.click()
  await page.getByRole('menuitem', { name: 'Remove plan' }).click()
  await expect(page.getByRole('menu')).toHaveCount(0)
  await expect(page.getByRole('dialog', { name: 'Remove rate plan' })).toBeVisible()
  await page.getByRole('button', { name: 'Cancel' }).click()

  await trigger.click()
  await page.getByRole('menuitem', { name: 'End current assignment' }).click()
  await expect(page.getByRole('menu')).toHaveCount(0)
  await expect(page.getByRole('dialog', { name: 'End current assignment' })).toBeVisible()
})

test('reviewed bill applies its plan and cycle and refreshes Billing', async ({ page }) => {
  const observed = await mockRepairServer(page)
  await page.goto('/billing?action=upload')
  const dialog = page.getByRole('dialog', { name: 'Upload electric bill' })
  await page.locator('input[type="file"]').setInputFiles(path.resolve('../backend/tests/fixtures/bills/sanitized-sce-domestic-bill.pdf'))
  await page.getByRole('button', { name: 'Upload and review' }).click()
  await page.getByRole('button', { name: 'Confirm extracted values' }).click()
  await expect(page.getByRole('heading', { name: 'Confirm the reviewed values' })).toBeVisible()
  await page.getByRole('checkbox', { name: 'I reviewed these values and want to continue.' }).check()
  await page.getByRole('button', { name: 'Continue to Apply' }).click()
  await page.getByRole('button', { name: 'Apply plan and billing cycle' }).click()
  await expect(page.getByRole('heading', { name: 'Bill applied' })).toBeVisible()
  expect(observed.billPublished).toBe(true)
  expect(observed.cycleImported).toBe(true)
  await page.getByRole('button', { name: 'Return to Billing' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByRole('table').locator('tbody').getByText('Imported', { exact: true })).toBeVisible()
})

test('Owner completes the current-plan, revision, lifecycle, adjustment, and source-check workflow', async ({ page }) => {
  test.setTimeout(60_000)
  const browserErrors: string[] = []
  page.on('pageerror', (error) => {
    browserErrors.push(error.message)
  })
  page.on('console', (message) => {
    const text = message.text()
    const expectedConflict =
      text.includes('Failed to load resource') && text.includes('409')
    if (message.type() === 'error' && !expectedConflict) {
      browserErrors.push(text)
    }
  })
  const observed = await mockRepairServer(page, false, { sourceDelayMs: 400 })
  await page.goto('/billing?advanced=rates')
  const library = page.locator('.rate-plan-library')
  await expect(library.getByText(/Current v2/)).toHaveCount(1)
  const replacementRow = library
    .getByRole('listitem')
    .filter({ hasText: 'Summer adjustment candidate' })
  await expect(replacementRow).toContainText('Published v1')
  await expect(replacementRow).toContainText('Not current')

  await replacementRow.getByRole('button', { name: 'Replace current' }).click()
  const replacementPanel = page.getByRole('region', { name: 'Replace current plan' })
  await expect(replacementPanel).toContainText('Historical costs')
  await expect(replacementPanel).toContainText('Preserved')
  await replacementPanel.getByRole('button', { name: 'Replace current' }).click()
  await expect.poll(() => observed.assignmentRequestCount).toBe(1)

  const currentPlanRow = page
    .getByRole('listitem')
    .filter({ hasText: 'TOU-D 4 PM to 9 PM' })
  await currentPlanRow.getByRole('button', { name: 'Adjust rates' }).click()
  let editor = page.locator('.rate-editor-shell')
  await editor.getByRole('tab', { name: '9 Preview' }).click()
  await expect(
    editor.getByRole('region', { name: 'Rate revision comparison' }),
  ).toContainText('Published v2 compared with Draft v3')
  await expect(
    editor.getByRole('region', { name: 'Rate revision comparison' }),
  ).toContainText('Same plan identity')
  await expect(page).toHaveScreenshot('rate-editor-adjust-rates-compare.png', {
    fullPage: true,
    animations: 'disabled',
  })
  await editor.getByRole('button', { name: 'Close editor' }).click()

  await page.getByRole('button', { name: 'New plan' }).click()
  editor = page.locator('.rate-editor-shell')
  await expect(editor.getByRole('tab')).toHaveCount(10)
  await expect(editor.getByText('Plan details', { exact: true })).toBeVisible()
  await expect(page).toHaveScreenshot('rate-editor-details.png', { fullPage: true, animations: 'disabled' })
  await editor.getByRole('tab', { name: '5 TOU schedules' }).click()
  await expect(editor.getByText('Time-of-use schedules', { exact: true })).toBeVisible()
  await expect(page).toHaveScreenshot('rate-editor-schedules.png', { fullPage: true, animations: 'disabled' })
  await editor.getByRole('tab', { name: '8 Assignment' }).click()
  await editor.getByRole('checkbox', { name: /I understand this replaces/i }).check()
  await editor.getByRole('tab', { name: '10 Publish' }).click()
  await expect(editor.getByText('Save, publish, and assign')).toBeVisible()
  await expect(page).toHaveScreenshot('rate-editor-lifecycle.png', { fullPage: true, animations: 'disabled' })
  const lifecycle = editor.locator('.lifecycle-steps')
  await lifecycle.getByRole('button', { name: 'Save draft' }).click()
  await lifecycle.getByRole('button', { name: 'Validate' }).click()
  await lifecycle.getByRole('button', { name: 'Publish version' }).click()
  await lifecycle.getByRole('button', { name: 'Replace current' }).click()
  await expect.poll(() => observed.assignment).toBeTruthy()
  expect(observed.rateDraft?.schema_version).toBe('power-monitor-rate-plan/1.0')
  expect((observed.rateDraft?.seasons as Array<{ schedules: Array<{ periods: Array<{ price_per_kwh: unknown }> }> }>)[0]?.schedules[0]?.periods[0]?.price_per_kwh).toBe('0.25000000')
  expect(observed.assignment?.rate_version_id).toBe('draft-version')
  await editor.getByRole('button', { name: 'Close editor' }).click()

  await page.getByRole('tab', { name: 'Versions', exact: true }).click()
  const unusedDraftRow = page
    .getByRole('listitem')
    .filter({ hasText: /Summer adjustment candidate.*v2/ })
  await unusedDraftRow.getByRole('button', { name: 'Lifecycle' }).click()
  await page.getByLabel('Type version 2').fill('2')
  await page.getByRole('button', { name: 'Delete unused draft' }).click()
  await expect.poll(() => observed.draftDeleted).toBe(true)

  const currentVersionRow = page
    .getByRole('listitem')
    .filter({ hasText: /TOU-D 4 PM to 9 PM.*v2/ })
  await currentVersionRow.getByRole('button', { name: 'Lifecycle' }).click()
  await page.getByLabel('Type version 2').fill('2')
  await expect(page.getByRole('button', { name: 'Remove', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Cancel' }).click()

  const versionRow = page
    .getByRole('listitem')
    .filter({ hasText: /Summer adjustment candidate.*v1/ })
  await versionRow.getByRole('button', { name: 'Lifecycle' }).click()
  await page.getByLabel('Type version 1').fill('1')
  await page.getByRole('button', { name: 'Retire', exact: true }).click()
  await expect.poll(() => observed.retired).toBe(true)

  await page.getByRole('tab', { name: 'Custom editor', exact: true }).click()
  const removablePlanRow = page.getByRole('listitem').filter({ hasText: 'Summer adjustment candidate' })
  await removablePlanRow.getByRole('button', { name: 'Lifecycle' }).click()
  const lifecyclePanel = page.getByRole('region', { name: 'Lifecycle controls for Summer adjustment candidate' })
  await expect(lifecyclePanel).toBeVisible()
  await lifecyclePanel.getByLabel('Type SUMMER-ADJUST').fill('SUMMER-ADJUST')
  await page.getByRole('button', { name: 'Remove plan' }).click()
  await expect.poll(() => observed.removed).toBe(true)

  await page.getByRole('tab', { name: 'Sources', exact: true }).click()
  await expect(page.getByText('sce.com · Official source')).toBeVisible()
  await expect(page.getByText('urn:power-monitor:utility-bill:service-1')).toBeHidden()
  await expect(page).toHaveScreenshot('rate-editor-sources.png', { fullPage: true, animations: 'disabled' })
  const checkButton = page.getByRole('button', {
    name: 'Check rate sources now',
  })
  await checkButton.click()
  await expect.poll(() => observed.sourceCheckRequests).toBe(1)
  await expect(checkButton).toBeDisabled()
  await checkButton.evaluate((element) => {
    const button = element as HTMLButtonElement
    button.click()
    button.click()
  })
  await expect(page.getByRole('heading', { name: 'Source check completed' })).toBeVisible()
  await expect(page.getByText(/1 of 1 sources.*1 candidates.*1 archived/)).toBeVisible()
  expect(observed.sourceCheckRequests).toBe(1)
  await page.getByRole('button', { name: 'Add source' }).click()
  await page.getByLabel('Name').fill('SCE official tariff')
  await page.getByLabel('Approved HTTPS URL').fill('https://www.sce.com/rates/tariffs')
  await page.getByRole('button', { name: 'Add approved source' }).click()
  await expect.poll(() => observed.sourceAdded).toBe(true)

  await page.getByRole('tab', { name: 'Removed', exact: true }).click()
  await page.getByRole('button', { name: 'Restore' }).first().click()
  await expect.poll(() => observed.restored).toBe(true)

  await page.getByRole('tab', { name: 'Custom editor', exact: true }).click()
  await replacementRow.getByRole('button', { name: 'Replace current' }).click()
  await replacementPanel.getByLabel('Effective timing').selectOption('custom')
  await replacementPanel.getByLabel('Effective from').fill('2026-08-01T00:00')
  await replacementPanel.getByRole('button', { name: 'Replace current' }).click()
  await expect(page.getByText(/overlaps assignment assignment-current/i)).toBeVisible()
  await expect.poll(() => observed.assignmentRequestCount).toBe(3)
  await replacementPanel.getByRole('button', { name: 'Cancel' }).click()

  await page.getByRole('tab', { name: 'Adjustments', exact: true }).click()
  await page.getByLabel('Value').fill('0.01500000')
  await page.getByLabel('Reason').fill('Reviewed seasonal delivery adjustment')
  await page.getByLabel('Evidence reference (optional)').fill('SCE tariff review 2026-07')
  await page.getByRole('button', { name: 'Add adjustment' }).click()
  await expect.poll(() => observed.adjustmentCreated).toBe(true)
  const adjustmentRow = page.getByRole('listitem').filter({ hasText: 'Custom Per Kwh' })
  await adjustmentRow.getByRole('button', { name: 'Edit' }).click()
  await page.getByLabel('Value').fill('0.01700000')
  await page.getByLabel('Reason').fill('Corrected after second tariff review')
  await page.getByRole('button', { name: 'Save revision' }).click()
  await expect.poll(() => observed.adjustmentUpdated).toBe(true)
  await adjustmentRow.getByRole('button', { name: 'Remove' }).click()
  await expect.poll(() => observed.adjustmentRemoved).toBe(true)
  expect(browserErrors).toEqual([])
})

test('source checks and manual adjustments expose their complete observable lifecycle', async ({ page }) => {
  const observed = await mockRepairServer(page, true)
  await page.goto('/billing?advanced=rates')

  await page.getByRole('tab', { name: 'Sources', exact: true }).click()
  await page
    .getByRole('button', { name: 'Check rate sources now' })
    .click()
  await expect(page.getByRole('heading', { name: 'Source check completed' })).toBeVisible()
  await expect(page.getByText(/1 of 1 sources.*1 candidates.*1 archived/)).toBeVisible()
  await expect(page.getByText('SCE Residential TOU Page', { exact: true }).last()).toBeVisible()
  await expect(page).toHaveScreenshot('rate-editor-source-check-completed.png', {
    fullPage: true,
    animations: 'disabled',
  })
  expect(observed.sourceCheckStarted).toBe(true)

  await page.getByRole('tab', { name: 'Adjustments', exact: true }).click()
  await page.getByLabel('Value').fill('0.01500000')
  await page.getByLabel('Reason').fill('Reviewed seasonal delivery adjustment')
  await page.getByLabel('Evidence reference (optional)').fill('SCE tariff review 2026-07')
  await page.getByRole('button', { name: 'Add adjustment' }).click()
  await expect.poll(() => observed.adjustmentCreated).toBe(true)
  const row = page.getByRole('listitem').filter({ hasText: 'Custom Per Kwh' })
  await expect(row).toContainText('Reviewed seasonal delivery adjustment')

  await row.getByRole('button', { name: 'Edit' }).click()
  await page.getByLabel('Value').fill('0.01700000')
  await page.getByLabel('Reason').fill('Corrected after second tariff review')
  await page.getByRole('button', { name: 'Save revision' }).click()
  await expect.poll(() => observed.adjustmentUpdated).toBe(true)
  await expect(row).toContainText('0.01700000')

  await row.getByRole('button', { name: 'Remove' }).click()
  await expect.poll(() => observed.adjustmentRemoved).toBe(true)
  await expect(page.getByText('No manual adjustments')).toBeVisible()
})

test('bill importer exposes a recoverable error state and retries the same file', async ({ page }) => {
  await mockRepairServer(page, false, { failFirstBillUpload: true })
  await page.goto('/billing?action=upload')
  await page.locator('input[type="file"]').setInputFiles(path.resolve('../backend/tests/fixtures/bills/sanitized-sce-domestic-bill.pdf'))
  await page.getByRole('button', { name: 'Upload and review' }).click()
  await expect(page.getByRole('alert')).toContainText('Retry the reviewed upload.')
  await expect(page).toHaveScreenshot('importer-error.png', { fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: 'Retry this step' }).click()
  await expect(page.getByText('Plan name')).toBeVisible()
})

test('Home has intentional empty and configured dashboard layouts', async ({ page }) => {
  await mockRepairServer(page)
  await page.goto('/home')
  await expect(page.getByText('Connect your first sensor')).toBeVisible()
  await expect(page.locator('.home-onboarding-grid')).toBeVisible()
  await expect(page).toHaveScreenshot('home-repair-empty.png', { fullPage: true, animations: 'disabled' })
  await page.unroute('**/api/v1/**')
  await mockRepairServer(page, false, { billingOnly: true })
  await page.reload()
  await expect(page.locator('[data-metric-identity="home.current_plan"]')).toContainText('TOU-D 4 PM to 9 PM')
  await expect(page.locator('[data-metric-identity="home.live_status"]')).toContainText('Not connected')
  await expect(page.getByText('Billing setup complete')).toBeVisible()
  await expect(page).toHaveScreenshot('home-repair-billing-no-live.png', { fullPage: true, animations: 'disabled' })
  await page.unroute('**/api/v1/**')
  await mockRepairServer(page, true)
  await page.reload()
  await expect(page.getByText('Live power')).toBeVisible()
  await expect(page.getByText('Billing snapshot')).toBeVisible()
  await expect(page).toHaveScreenshot('home-repair-connected.png', { fullPage: true, animations: 'disabled' })
})

test('History preserves intentional no-data and configured layouts', async ({ page }, testInfo) => {
  await mockRepairServer(page)
  await page.goto('/history')
  await expect(page.getByRole('heading', { name: 'History' })).toBeVisible()
  await expect(page).toHaveScreenshot('history-no-data.png', { fullPage: true, animations: 'disabled' })
  await page.unroute('**/api/v1/**')
  await mockRepairServer(page, true)
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Whole Home', exact: true })).toBeVisible()
  await expect(page.getByText('60.34%')).toHaveCount(3)
  await expect(page.getByText('60.3444444444444%', { exact: true })).toHaveCount(0)
  await page.getByText('View accessible data table').click()
  await expect(page.getByRole('cell', { name: '60.34%' })).toBeVisible()
  await expect(page).toHaveScreenshot('history-data.png', { fullPage: true, animations: 'disabled' })
  if (process.env.UPDATE_BACKUP_LIVE_DOCS === '1' && testInfo.project.name === 'desktop') {
    await page.screenshot({
      path: path.resolve('..', 'docs', 'screenshots', 'history-coverage-formatting.png'),
      fullPage: false,
      animations: 'disabled',
    })
  }
})

test('repair surfaces do not overflow or overlap at the active viewport', async ({ page }) => {
  await mockRepairServer(page, true)
  await page.goto('/home')
  await expectNoDocumentOverflow(page)
  await page.goto('/billing?advanced=rates')
  await page.getByRole('button', { name: 'New plan' }).click()
  await expectNoDocumentOverflow(page)
  await page.getByRole('button', { name: 'Upload electric bill' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expectNoDocumentOverflow(page)
})

async function expectNoDocumentOverflow(page: Page) {
  const width = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    offenders: [...document.querySelectorAll<HTMLElement>('body *')].filter((element) => element.getBoundingClientRect().right > document.documentElement.clientWidth + 1).slice(0, 8).map((element) => ({ tag: element.tagName, className: element.className, right: element.getBoundingClientRect().right, width: element.getBoundingClientRect().width })),
  }))
  expect(width.overflow, JSON.stringify(width.offenders)).toBeLessThanOrEqual(1)
  const collisions = await page.locator('.home-status-grid > .metric, .rate-editor-footer > *, .workflow-footer > *').evaluateAll((elements) => {
    const visible = elements.filter((element) => (element as HTMLElement).offsetParent !== null)
    const boxes = visible.map((element) => element.getBoundingClientRect())
    return boxes.flatMap((first, firstIndex) => boxes.slice(firstIndex + 1).flatMap((second) => {
      const overlapX = Math.min(first.right, second.right) - Math.max(first.left, second.left)
      const overlapY = Math.min(first.bottom, second.bottom) - Math.max(first.top, second.top)
      return overlapX > 1 && overlapY > 1 ? [{ firstIndex, overlapX, overlapY }] : []
    }))
  })
  expect(collisions).toEqual([])
}
