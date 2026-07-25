import { describe, expect, it } from 'vitest'
import {
  adaptBillDetail,
  adaptElectricServices,
  adaptFamily,
  adaptFamilyRoles,
  adaptHistory,
  adaptPermissions,
  adaptRateAssignments,
  adaptRateEvidence,
  adaptRatePlanDependencies,
  adaptRateSources,
  adaptRateVersions,
  resolveSingleHome,
} from '../src/api/adapters'

describe('typed homeowner adapters', () => {
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
      status: 'draft',
      effective_from: '2026-07-24',
      pricing_model: 'time_of_use_tiered',
      integrity_sha256: 'abc123',
      immutable_after_use: false,
    }])[0]).toMatchObject({ id: 'version-1', version: 3, pricingModel: 'time_of_use_tiered', integritySha256: 'abc123' })
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
