import type { HistoryFilters, Home } from '../../types/models'

// SSE provides the low-latency path. This bounded poll keeps charts moving
// when a browser, proxy, or network transition leaves the event stream open
// but no longer delivering reading events.
export const HISTORY_REFETCH_INTERVAL_MS = 60_000

export function historyQueryKey(
  filters: HistoryFilters,
  homeId?: string,
  cycleStart?: string,
  cycleEnd?: string,
  window?: HistoryWindow,
): readonly unknown[] {
  const key: unknown[] = [
    'history',
    'page',
    homeId ?? null,
    filters.range,
    filters.metric,
    filters.scope,
    filters.sensorId ?? null,
    filters.customStart ?? null,
    filters.customEnd ?? null,
    window?.start.toISOString() ?? null,
    window?.end.toISOString() ?? null,
    window?.bucket ?? null,
  ]
  if (filters.range === 'cycle') key.push(cycleStart ?? null, cycleEnd ?? null)
  return key
}

export interface HistoryWindow {
  start: Date
  end: Date
  bucket: '5m' | '15m' | '1h' | '1d'
}

function zonedDateTime(value: string, timezone: string): Date {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/u.exec(value)
  if (!match) return new Date(value)
  const desired = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]), Number(match[4] ?? 0), Number(match[5] ?? 0))
  let result = desired
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).formatToParts(new Date(result))
    const part = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find((item) => item.type === type)?.value ?? 0)
    const represented = Date.UTC(part('year'), part('month') - 1, part('day'), part('hour'), part('minute'))
    result += desired - represented
  }
  return new Date(result)
}

export function historyWindow(
  filters: HistoryFilters,
  cycleStart?: string,
  cycleEnd?: string,
  timezone = 'UTC',
  now = new Date(),
): HistoryWindow {
  if (filters.range === 'today') {
    const parts = new Intl.DateTimeFormat('en-US', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now)
    const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? ''
    const day = `${value('year')}-${value('month')}-${value('day')}`
    return { start: zonedDateTime(day, timezone), end: now, bucket: '15m' }
  }
  if (filters.range === '7d') return { start: new Date(now.getTime() - 7 * 86_400_000), end: now, bucket: '1h' }
  if (filters.range === '30d') return { start: new Date(now.getTime() - 30 * 86_400_000), end: now, bucket: '1d' }
  if (filters.range === 'cycle' && cycleStart) {
    return { start: new Date(cycleStart), end: cycleEnd ? new Date(Math.min(now.getTime(), new Date(cycleEnd).getTime())) : now, bucket: '1d' }
  }
  const start = filters.customStart ? zonedDateTime(filters.customStart, timezone) : new Date(now.getTime() - 86_400_000)
  const end = filters.customEnd ? zonedDateTime(filters.customEnd, timezone) : now
  return { start, end, bucket: end.getTime() - start.getTime() > 14 * 86_400_000 ? '1d' : '1h' }
}

export function historyPayload(
  filters: HistoryFilters,
  home: Home,
  cycleStart?: string,
  cycleEnd?: string,
  requestWindow?: HistoryWindow,
): Record<string, unknown> {
  const window = requestWindow ?? historyWindow(filters, cycleStart, cycleEnd, home.timezone)
  const metrics = filters.metric === 'power'
    ? ['power_w']
    : filters.metric === 'energy'
      ? ['energy_kwh', 'power_w']
      : filters.metric === 'cost'
        ? ['energy_cost', 'power_w']
        : ['energy_kwh', 'energy_cost', 'power_w']
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
