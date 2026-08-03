import { describe, expect, it } from 'vitest'
import { canAccessSettings, hasPermission, ROUTE_POLICIES, satisfiesPolicy } from '../src/access/permissions'
import type { UserSession } from '../src/types/models'

function session(permissions: string[]): UserSession {
  return { authenticated: true, bootstrapRequired: false, user: { id: 'viewer-1', email: 'viewer@example.test', name: 'Viewer', roles: ['viewer'], permissions, allHomes: true, homeIds: [], accessRevision: 2 } }
}

describe('permission-driven navigation policies', () => {
  const strictViewer = session(['overview.view', 'usage.view', 'history.view', 'costs.view', 'sites.view', 'utility_accounts.view', 'topology.view', 'devices.view', 'rates.view', 'alerts.view', 'status_indicators.view'])

  it('allows the three read-only workspaces without inferring authority from the role name', () => {
    expect(satisfiesPolicy(strictViewer, ROUTE_POLICIES.home)).toBe(true)
    expect(satisfiesPolicy(strictViewer, ROUTE_POLICIES.history)).toBe(true)
    expect(satisfiesPolicy(strictViewer, ROUTE_POLICIES.billing)).toBe(true)
    expect(canAccessSettings(strictViewer)).toBe(false)
  })

  it('does not grant export or mutations merely because the role is Viewer', () => {
    expect(hasPermission(strictViewer, 'history.export')).toBe(false)
    expect(hasPermission(strictViewer, 'costs.export')).toBe(false)
    expect(hasPermission(strictViewer, 'utility_bills.manage')).toBe(false)
    expect(hasPermission(strictViewer, 'devices.manage')).toBe(false)
    expect(hasPermission(strictViewer, 'alerts.acknowledge')).toBe(false)
  })

  it('uses effective permissions for custom roles rather than role display names', () => {
    const custom = session(['overview.view', 'settings.view'])
    if (custom.user) custom.user.roles = ['custom-household-auditor']
    expect(satisfiesPolicy(custom, ROUTE_POLICIES.home)).toBe(true)
    expect(canAccessSettings(custom)).toBe(true)
  })

  it('keeps firmware viewing, upload management, and deployment as separate authorities', () => {
    const firmwareViewer = session(['firmware.view'])
    const firmwareManager = session(['firmware.view', 'firmware.manage'])
    const firmwareDeployer = session(['firmware.view', 'firmware.manage', 'firmware.deploy'])

    expect(hasPermission(firmwareViewer, 'firmware.manage')).toBe(false)
    expect(hasPermission(firmwareViewer, 'firmware.deploy')).toBe(false)
    expect(hasPermission(firmwareManager, 'firmware.deploy')).toBe(false)
    expect(hasPermission(firmwareDeployer, 'firmware.deploy')).toBe(true)
  })
})
