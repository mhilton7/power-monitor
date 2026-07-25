import { describe, expect, it } from 'vitest'
import {
  adaptBillDetail,
  adaptElectricServices,
  adaptFamily,
  adaptFamilyRoles,
  adaptHistory,
  adaptPermissions,
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
})
