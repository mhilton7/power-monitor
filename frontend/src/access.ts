import type { Session } from './types'

const ADMIN_FALLBACK = new Set([
  'overview.view', 'devices.view', 'topology.view', 'history.view', 'history.export',
  'rates.view', 'rates.manage_custom', 'rates.manage_sources', 'rates.check_sources',
  'rates.review_candidates', 'rates.approve_candidates', 'rates.assign', 'alerts.view',
  'alerts.acknowledge', 'alerts.manage_rules', 'alerts.manage_delivery', 'enrollment.view',
  'enrollment.manage', 'sites.view', 'sites.manage', 'backups.view', 'logs.export',
  'users.view', 'users.manage', 'roles.view', 'roles.manage', 'audit.view',
  'settings.view', 'settings.manage', 'interface_text.view', 'interface_text.manage',
])

const ROLE_FALLBACK: Record<string, Set<string>> = {
  viewer: new Set(['overview.view', 'devices.view', 'topology.view', 'history.view', 'history.export', 'rates.view', 'alerts.view', 'sites.view']),
  operator: new Set(['overview.view', 'devices.view', 'devices.manage', 'topology.view', 'topology.manage', 'history.view', 'history.export', 'rates.view', 'alerts.view', 'alerts.acknowledge', 'alerts.manage_rules', 'enrollment.view', 'enrollment.manage', 'sites.view']),
  'rate-manager': new Set(['overview.view', 'devices.view', 'topology.view', 'history.view', 'history.export', 'rates.view', 'rates.manage_custom', 'rates.manage_sources', 'rates.check_sources', 'rates.review_candidates', 'rates.approve_candidates', 'rates.assign', 'alerts.view', 'sites.view']),
}

export function sessionPermissions(session: Session): Set<string> {
  if (session.user?.permissions) return new Set(session.user.permissions)
  if (session.user?.roles.includes('admin')) return ADMIN_FALLBACK
  const permissions = new Set<string>()
  for (const role of session.user?.roles ?? []) {
    for (const permission of ROLE_FALLBACK[role] ?? []) permissions.add(permission)
  }
  return permissions
}

export function hasPermission(session: Session, permission: string): boolean {
  return sessionPermissions(session).has(permission)
}
