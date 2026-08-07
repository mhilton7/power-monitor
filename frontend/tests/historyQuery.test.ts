import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  HISTORY_REFETCH_INTERVAL_MS,
  historyPayload,
  historyQueryKey,
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

  it('keeps query keys stable when inactive billing-cycle inputs change', () => {
    expect(historyQueryKey(today, home.id, '2026-07-01', '2026-08-01')).toEqual(
      historyQueryKey(today, home.id, '2026-06-01', '2026-07-01'),
    )
    const billingCycle: HistoryFilters = { ...today, range: 'cycle' }
    expect(historyQueryKey(billingCycle, home.id, '2026-07-01', '2026-08-01')).not.toEqual(
      historyQueryKey(billingCycle, home.id, '2026-06-01', '2026-07-01'),
    )
  })

  it('requests only the pricing work needed for the selected metric plus peak power', () => {
    expect(historyPayload(today, home).metrics).toEqual(['energy_kwh', 'power_w'])
    expect(historyPayload({ ...today, metric: 'cost' }, home).metrics).toEqual([
      'energy_cost',
      'power_w',
    ])
    expect(historyPayload({ ...today, metric: 'power' }, home).metrics).toEqual(['power_w'])
  })

  it('binds the query key and payload to the same exact request window', () => {
    const firstWindow = {
      start: new Date('2026-07-24T00:00:00Z'),
      end: new Date('2026-07-31T00:00:00Z'),
      bucket: '1h' as const,
    }
    const secondWindow = {
      ...firstWindow,
      end: new Date('2026-07-31T00:01:00Z'),
    }
    const firstKey = historyQueryKey(today, home.id, undefined, undefined, firstWindow)
    const secondKey = historyQueryKey(today, home.id, undefined, undefined, secondWindow)
    const payload = historyPayload(today, home, undefined, undefined, firstWindow)

    expect(firstKey).not.toEqual(secondKey)
    expect(payload.start_utc).toBe('2026-07-24T00:00:00.000Z')
    expect(payload.end_utc).toBe('2026-07-31T00:00:00.000Z')
    expect(payload.bucket).toBe('1h')
  })
})
