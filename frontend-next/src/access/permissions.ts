import type { UserSession } from '../types/models'

export function hasPermission(session: UserSession, permission: string): boolean {
  const user = session.user
  if (!user) return false
  if (user.roles.includes('admin')) return true
  return user.permissions.includes(permission)
}

export function isOwner(session: UserSession): boolean {
  return Boolean(session.user?.roles.some((role) => role === 'admin' || role.toLowerCase().includes('owner')))
}
