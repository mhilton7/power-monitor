import { describe, expect, it } from 'vitest'
import { ACTION_REGISTRY, findDuplicateActions } from '../src/actions'
import { filterSelectableSites } from '../src/components/Layout'
import { pageFromPath } from '../src/components/StatusIndicators'
import type { Session, Site } from '../src/types'
import { canOpenWorkspace, permittedTabs, WORKSPACES, workspaceById } from '../src/workspaces'

const adminSession: Session = {
  authenticated: true,
  bootstrap_required: false,
  user: {
    id: 'admin',
    email: 'admin@example.com',
    display_name: 'Admin',
    roles: ['admin'],
    all_sites: true,
  },
}

const viewerSession: Session = {
  authenticated: true,
  bootstrap_required: false,
  user: {
    id: 'viewer',
    email: 'viewer@example.com',
    display_name: 'Viewer',
    roles: ['viewer'],
    permissions: ['overview.view', 'devices.view', 'history.view', 'rates.view'],
  },
}

describe('six canonical workspaces', () => {
  it('publishes exactly the required top-level destinations', () => {
    expect(WORKSPACES.map((workspace) => workspace.label)).toEqual([
      'Overview',
      'Monitoring',
      'Analytics',
      'Billing',
      'Alerts',
      'Administration',
    ])
    expect(WORKSPACES.map((workspace) => workspace.route)).toEqual([
      '/overview',
      '/monitoring',
      '/analytics',
      '/billing',
      '/alerts',
      '/administration',
    ])
  })

  it('keeps bill import out of Billing tabs and inside Custom Plan', () => {
    const billing = workspaceById('billing')
    expect(billing.tabs.map((tab) => tab.label)).toEqual([
      'Utility Accounts',
      'Rate Plans',
      'Rate Sources',
    ])
    expect(billing.tabs.some((tab) => /bill import/i.test(tab.label))).toBe(false)
    expect(ACTION_REGISTRY['rate_plan.import_from_bill'].route).toBe(
      '/billing/rate-plans/new?bill_import=open',
    )
  })

  it('hides management workspaces and tabs from a read-only viewer', () => {
    expect(canOpenWorkspace(workspaceById('billing'), viewerSession)).toBe(false)
    expect(canOpenWorkspace(workspaceById('administration'), viewerSession)).toBe(false)
    expect(permittedTabs(workspaceById('monitoring'), viewerSession).map((tab) => tab.id)).toEqual([
      'devices',
    ])
    expect(canOpenWorkspace(workspaceById('administration'), adminSession)).toBe(true)
  })

  it('maps canonical paths to the existing status engine pages', () => {
    expect(pageFromPath('/overview')).toBe('overview')
    expect(pageFromPath('/monitoring/devices/device-1')).toBe('device_detail')
    expect(pageFromPath('/analytics/history')).toBe('history')
    expect(pageFromPath('/billing/rate-sources')).toBe('rate_sources')
    expect(pageFromPath('/administration/security?view=health')).toBe('system_health')
  })
})

describe('canonical action identity', () => {
  it('reports duplicate page actions but permits the same row action for distinct resources', () => {
    const root = document.createElement('div')
    root.innerHTML = `
      <button data-action-id="site.create"></button>
      <button data-action-id="site.create"></button>
      <button data-action-id="site.view" data-action-resource="site-a"></button>
      <button data-action-id="site.view" data-action-resource="site-b"></button>
    `
    expect(findDuplicateActions(root)).toEqual([{ actionId: 'site.create', count: 2 }])
  })

  it('reports a repeated row action for the same resource', () => {
    const root = document.createElement('div')
    root.innerHTML = `
      <button data-action-id="site.remove" data-action-resource="site-a"></button>
      <button data-action-id="site.remove" data-action-resource="site-a"></button>
    `
    expect(findDuplicateActions(root)).toEqual([{ actionId: 'site.remove', count: 2 }])
  })
})

describe('global site selector', () => {
  it('filters a large site list by name or code while retaining the selected site', () => {
    const sites = Array.from({ length: 10 }, (_, index): Site => ({
      id: `site-${index + 1}`,
      name: index === 8 ? 'North Campus' : `Facility ${index + 1}`,
      code: index === 8 ? 'NORTH' : `FAC-${index + 1}`,
      timezone: 'America/Los_Angeles',
      allowed_cidrs: [],
      allowed_domains: [],
      allow_public_polling: false,
      lifecycle_state: 'active',
      is_default: index === 0,
    }))

    expect(filterSelectableSites(sites, 'site-1', 'north').map((site) => site.id)).toEqual([
      'site-1',
      'site-9',
    ])
    expect(filterSelectableSites(sites, 'site-1', 'fac-10').map((site) => site.id)).toEqual([
      'site-1',
      'site-10',
    ])
  })
})
