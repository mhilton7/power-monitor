import { describe, expect, it } from 'vitest'
import {
  groupNotifications,
  isCurrentAttentionNotification,
  removeCachedNotification,
  updateCachedNotification,
} from '../src/features/alerts/notificationSelectors'
import type { AlertSummary } from '../src/types/models'

const base: AlertSummary = {
  id: 'active-alert',
  code: 'heartbeat_stale',
  kind: 'operational_alert',
  category: 'connectivity',
  title: 'Indoor-AC stopped reporting',
  message: 'No signed heartbeat has been received.',
  severity: 'warning',
  status: 'open',
  openedAt: '2026-07-31T16:00:00Z',
  lastSeenAt: '2026-07-31T16:01:00Z',
  occurrenceCount: 1,
  affectedResource: { type: 'sensor', id: 'sensor-1', name: 'Indoor-AC' },
  evidence: [],
  impact: 'Live values may be stale.',
  remediation: { summary: 'Check the sensor.', steps: ['Confirm power.'] },
  suppression: {
    dismissible: true,
    permanentlySuppressible: false,
    currentlySuppressed: false,
    allowedScopes: [],
  },
}

describe('notification selectors', () => {
  it('updates one cached notification immediately without changing the page total', () => {
    const other = { ...base, id: 'other-alert' }
    const updated = updateCachedNotification(
      { items: [base, other], total: 2 },
      base.id,
      (notification) => ({ ...notification, status: 'acknowledged' }),
    )

    expect(updated?.items[0]?.status).toBe('acknowledged')
    expect(updated?.items[1]?.status).toBe('open')
    expect(updated?.total).toBe(2)
  })

  it('removes one cached notification and updates the page total', () => {
    const other = { ...base, id: 'other-alert' }
    const updated = removeCachedNotification(
      { items: [base, other], total: 2 },
      base.id,
    )

    expect(updated?.items.map((item) => item.id)).toEqual(['other-alert'])
    expect(updated?.total).toBe(1)
  })

  it('keeps the dashboard and notification drawer on the same current-alert rules', () => {
    const acknowledged = { ...base, id: 'acknowledged', status: 'acknowledged' as const }
    const silenced = { ...base, id: 'silenced', status: 'silenced' as const }
    const resolved = { ...base, id: 'resolved', status: 'resolved' as const }
    const dismissed = { ...base, id: 'dismissed', status: 'dismissed' as const }
    const recommendation = {
      ...base,
      id: 'recommendation',
      kind: 'setup_recommendation' as const,
    }
    const grouped = groupNotifications([
      base,
      acknowledged,
      silenced,
      resolved,
      dismissed,
      recommendation,
    ])

    expect(grouped.active.map((item) => item.id)).toEqual([
      'active-alert',
      'acknowledged',
      'silenced',
    ])
    expect(grouped.recommendations.map((item) => item.id)).toEqual(['recommendation'])
    expect(grouped.resolved.map((item) => item.id)).toEqual(['resolved'])
    expect(grouped.resolvedAll.map((item) => item.id)).toEqual(['resolved'])
    expect(grouped.active.every(isCurrentAttentionNotification)).toBe(true)
  })

  it('limits the resolved preview without losing notifications selected by clear all', () => {
    const resolved = Array.from({ length: 12 }, (_, index) => ({
      ...base,
      id: `resolved-${index + 1}`,
      status: 'resolved' as const,
    }))

    const grouped = groupNotifications(resolved)

    expect(grouped.resolved).toHaveLength(10)
    expect(grouped.resolvedAll).toHaveLength(12)
    expect(grouped.resolvedAll.at(-1)?.id).toBe('resolved-12')
  })
})
