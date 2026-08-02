import type { AlertSummary } from '../../types/models'

const terminalStates = new Set<AlertSummary['status']>(['resolved', 'dismissed', 'suppressed'])

export interface NotificationPageCache {
  items: AlertSummary[]
  total: number
}

export function updateCachedNotification(
  page: NotificationPageCache | undefined,
  notificationId: string,
  update: (notification: AlertSummary) => AlertSummary,
): NotificationPageCache | undefined {
  if (!page) return page
  return {
    ...page,
    items: page.items.map((notification) => (
      notification.id === notificationId ? update(notification) : notification
    )),
  }
}

export function removeCachedNotification(
  page: NotificationPageCache | undefined,
  notificationId: string,
): NotificationPageCache | undefined {
  if (!page) return page
  const items = page.items.filter((notification) => notification.id !== notificationId)
  return {
    ...page,
    items,
    total: Math.max(0, page.total - (page.items.length - items.length)),
  }
}

export function isCurrentAttentionNotification(notification: AlertSummary): boolean {
  return notification.kind !== 'setup_recommendation' && !terminalStates.has(notification.status)
}

export function countCurrentAttentionNotifications(notifications: AlertSummary[]): number {
  return notifications.filter(isCurrentAttentionNotification).length
}

export function groupNotifications(notifications: AlertSummary[]) {
  const resolvedAll = notifications.filter((notification) => notification.status === 'resolved')
  return {
    active: notifications.filter(isCurrentAttentionNotification),
    recommendations: notifications.filter(
      (notification) => notification.kind === 'setup_recommendation'
        && !terminalStates.has(notification.status),
    ),
    resolved: resolvedAll.slice(0, 10),
    resolvedAll,
  }
}
