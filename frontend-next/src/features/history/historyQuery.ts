import type { HistoryFilters, Home } from '../../types/models'

export interface HistoryWindow {
  start: Date
  end: Date
  bucket: '5m' | '15m' | '1h' | '1d'
}

function startOfLocalDay(now: Date): Date {
  const value = new Date(now)
  value.setHours(0, 0, 0, 0)
  return value
}

export function historyWindow(filters: HistoryFilters, cycleStart?: string, cycleEnd?: string): HistoryWindow {
  const now = new Date()
  if (filters.range === 'today') return { start: startOfLocalDay(now), end: now, bucket: '15m' }
  if (filters.range === '7d') return { start: new Date(now.getTime() - 7 * 86_400_000), end: now, bucket: '1h' }
  if (filters.range === '30d') return { start: new Date(now.getTime() - 30 * 86_400_000), end: now, bucket: '1d' }
  if (filters.range === 'cycle' && cycleStart) {
    return { start: new Date(cycleStart), end: cycleEnd ? new Date(Math.min(now.getTime(), new Date(cycleEnd).getTime())) : now, bucket: '1d' }
  }
  const start = filters.customStart ? new Date(filters.customStart) : new Date(now.getTime() - 86_400_000)
  const end = filters.customEnd ? new Date(filters.customEnd) : now
  return { start, end, bucket: end.getTime() - start.getTime() > 14 * 86_400_000 ? '1d' : '1h' }
}

export function historyPayload(
  filters: HistoryFilters,
  home: Home,
  cycleStart?: string,
  cycleEnd?: string,
): Record<string, unknown> {
  const window = historyWindow(filters, cycleStart, cycleEnd)
  const metrics = filters.metric === 'power'
    ? ['power_w']
    : filters.metric === 'energy'
      ? ['energy_kwh']
      : filters.metric === 'cost'
        ? ['energy_cost']
        : ['energy_kwh', 'energy_cost']
  return {
    scope: filters.scope === 'sensor'
      ? { type: 'device', device_id: filters.sensorId }
      : { type: 'site', site_id: home.id },
    display_mode: 'combined',
    metrics,
    start_utc: window.start.toISOString(),
    end_utc: window.end.toISOString(),
    bucket: window.bucket,
    timezone: home.timezone,
    strict_coverage: false,
    page: 1,
    page_size: 500,
  }
}
