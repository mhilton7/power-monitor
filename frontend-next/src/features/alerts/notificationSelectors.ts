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

export function isCurrentAttentionNotification(notification: AlertSummary): boolean {
  return notification.kind !== 'setup_recommendation' && !terminalStates.has(notification.status)
}

export function groupNotifications(notifications: AlertSummary[]) {
  return {
    active: notifications.filter(isCurrentAttentionNotification),
    recommendations: notifications.filter(
      (notification) => notification.kind === 'setup_recommendation'
        && !terminalStates.has(notification.status),
    ),
    resolved: notifications.filter((notification) => notification.status === 'resolved').slice(0, 10),
  }
}
