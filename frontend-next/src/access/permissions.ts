import type { UserSession } from '../types/models'

export const PERMISSION_CODES = [
  'adjustments.manage',
  'alerts.acknowledge',
  'alerts.manage_delivery',
  'alerts.manage_rules',
  'alerts.view',
  'audit.view',
  'backups.create',
  'backups.delete',
  'backups.restore',
  'backups.verify',
  'backups.view',
  'costs.export',
  'costs.recalculate',
  'costs.view',
  'devices.manage',
  'devices.remove',
  'devices.view',
  'enrollment.manage',
  'enrollment.view',
  'firmware.manage',
  'firmware.view',
  'history.export',
  'history.view',
  'interface_text.manage',
  'interface_text.view',
  'logs.export',
  'network.manage',
  'network.view',
  'overview.view',
  'rates.approve_candidates',
  'rates.assign',
  'rates.check_sources',
  'rates.manage_custom',
  'rates.manage_sources',
  'rates.remove',
  'rates.restore',
  'rates.review_candidates',
  'rates.view',
  'roles.manage',
  'roles.view',
  'settings.manage',
  'settings.view',
  'sites.create',
  'sites.disable',
  'sites.edit',
  'sites.manage',
  'sites.remove',
  'sites.restore',
  'sites.set_default',
  'sites.transfer_resources',
  'sites.view',
  'sites.view_audit',
  'status_indicators.manage',
  'status_indicators.view',
  'topology.manage',
  'topology.view',
  'usage.view',
  'usage_imports.manage',
  'users.disable',
  'users.manage',
  'users.manage_protected',
  'users.remove',
  'users.restore',
  'users.view',
  'utility_accounts.manage',
  'utility_accounts.view',
  'utility_bills.manage',
  'utility_bills.view',
] as const

export type PermissionCode = typeof PERMISSION_CODES[number]
const PERMISSION_SET = new Set<string>(PERMISSION_CODES)

export interface PermissionPolicy {
  anyOf?: readonly PermissionCode[]
  allOf?: readonly PermissionCode[]
}

export const ROUTE_POLICIES = {
  home: { allOf: ['overview.view'] },
  history: { allOf: ['history.view'] },
  billing: { anyOf: ['costs.view', 'rates.view'] },
} as const satisfies Record<string, PermissionPolicy>

export const SETTINGS_SECTION_POLICIES = {
  home: { anyOf: ['sites.edit', 'settings.manage'] },
  sensors: { anyOf: ['devices.manage', 'devices.remove', 'topology.manage', 'enrollment.manage', 'firmware.manage'] },
  family: { allOf: ['users.view'] },
  notifications: { anyOf: ['alerts.manage_rules', 'alerts.manage_delivery'] },
  appearance: { anyOf: ['settings.view', 'settings.manage'] },
  data: { allOf: ['backups.view'] },
  advanced: {
    anyOf: [
      'network.manage', 'topology.manage', 'firmware.manage', 'interface_text.manage',
      'status_indicators.manage', 'audit.view', 'logs.export', 'settings.manage',
      'rates.manage_custom', 'rates.manage_sources', 'rates.check_sources',
    ],
  },
} as const satisfies Record<string, PermissionPolicy>

export function hasPermission(session: UserSession | undefined, permission: PermissionCode): boolean {
  return Boolean(session?.user?.permissions.includes(permission))
}

export function hasEveryPermission(session: UserSession | undefined, permissions: readonly PermissionCode[]): boolean {
  return permissions.every((permission) => hasPermission(session, permission))
}

export function hasAnyPermission(session: UserSession | undefined, permissions: readonly PermissionCode[]): boolean {
  return permissions.some((permission) => hasPermission(session, permission))
}

export function hasRequiredPermissionCodes(session: UserSession | undefined, permissions: readonly string[]): boolean {
  return permissions.every((permission) => PERMISSION_SET.has(permission)
    && hasPermission(session, permission as PermissionCode))
}

export function satisfiesPolicy(session: UserSession | undefined, policy: PermissionPolicy): boolean {
  return (!policy.allOf || hasEveryPermission(session, policy.allOf))
    && (!policy.anyOf || hasAnyPermission(session, policy.anyOf))
}

export function canAccessSettings(session: UserSession | undefined): boolean {
  return Object.values(SETTINGS_SECTION_POLICIES).some((policy) => satisfiesPolicy(session, policy))
}

export function roleLabel(session: UserSession | undefined): string {
  const roles = session?.user?.roles ?? []
  if (roles.includes('admin')) return 'Owner'
  if (roles.includes('rate-manager')) return 'Rate Manager'
  if (roles.includes('viewer')) return 'Viewer'
  if (roles.includes('operator')) return 'Family Member'
  return roles[0] ? 'Custom role' : 'Family Member'
}
