import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  HISTORY_REFETCH_INTERVAL_MS,
  historyPayload,
} from '../src/features/history/historyQuery'
import type { HistoryFilters, Home } from '../src/types/models'

const home: Home = {
  id: 'home-1',
  name: 'Upland Home',
  timezone: 'America/Los_Angeles',
  currency: 'USD',
  lifecycle: 'active',
  isDefault: true,
  revision: 1,
}

const today: HistoryFilters = {
  range: 'today',
  metric: 'energy',
  scope: 'home',
}

describe('moving history queries', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('regenerates the moving range end instead of freezing it at page load', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-31T23:00:00Z'))
    const initial = historyPayload(today, home)

    vi.setSystemTime(new Date('2026-08-01T00:01:00Z'))
    const refreshed = historyPayload(today, home)

    expect(initial.end_utc).toBe('2026-07-31T23:00:00.000Z')
    expect(refreshed.end_utc).toBe('2026-08-01T00:01:00.000Z')
    expect(refreshed.start_utc).toBe(initial.start_utc)
  })

  it('uses a bounded one-minute fallback alongside SSE invalidation', () => {
    expect(HISTORY_REFETCH_INTERVAL_MS).toBe(60_000)
  })
})
