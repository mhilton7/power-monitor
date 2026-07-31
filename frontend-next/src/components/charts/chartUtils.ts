import type { HistoryBucket, HistoryPoint } from '../../types/models'

export function colorWithAlpha(color: string, alpha: number): string {
  const normalized = /^#[0-9a-f]{6}$/iu.test(color) ? color.slice(1) : '78DFBF'
  const red = Number.parseInt(normalized.slice(0, 2), 16)
  const green = Number.parseInt(normalized.slice(2, 4), 16)
  const blue = Number.parseInt(normalized.slice(4, 6), 16)
  return `rgba(${red}, ${green}, ${blue}, ${Math.max(0, Math.min(1, alpha))})`
}

function zoned(timestamp: string | number, timezone: string, options: Intl.DateTimeFormatOptions): string {
  const date = new Date(timestamp)
  if (!Number.isFinite(date.getTime())) return 'Invalid timestamp'
  return new Intl.DateTimeFormat('en-US', { timeZone: timezone, ...options }).format(date)
}

function intervalParts(timestamp: string, timezone: string) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone, month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'shortOffset',
  }).formatToParts(new Date(timestamp))
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? ''
  return {
    day: `${value('month')} ${value('day')}`,
    clock: `${value('hour')}:${value('minute')}`,
    period: value('dayPeriod'),
    offset: value('timeZoneName'),
  }
}

export function chartTickLabel(timestamp: number, bucket: HistoryBucket, timezone: string): string {
  if (bucket === '1d') return zoned(timestamp, timezone, { month: 'short', day: 'numeric' })
  if (bucket === '1h') return zoned(timestamp, timezone, { month: 'short', day: 'numeric', hour: 'numeric' })
  return zoned(timestamp, timezone, { hour: 'numeric', minute: '2-digit' })
}

export function chartTickTimestamps(
  points: HistoryPoint[],
  rangeStart?: string,
  rangeEnd?: string,
  maximum = 12,
): number[] {
  const pointTimestamps = points
    .map((point) => Date.parse(point.start))
    .filter(Number.isFinite)
    .sort((left, right) => left - right)
    .filter((value, index, values) => index === 0 || value !== values[index - 1])
  const requestedStart = Date.parse(rangeStart ?? '')
  const requestedEnd = Date.parse(rangeEnd ?? '')
  const first = Number.isFinite(requestedStart) ? requestedStart : pointTimestamps[0]
  const last = Number.isFinite(requestedEnd) ? requestedEnd : pointTimestamps.at(-1)
  const minimumSeparation = first !== undefined && last !== undefined
    ? Math.max(0, (last - first) / Math.max(1, maximum - 1))
    : 0
  const candidates = [...pointTimestamps]
  if (Number.isFinite(requestedStart) && (pointTimestamps[0] === undefined || pointTimestamps[0] - requestedStart >= minimumSeparation)) {
    candidates.push(requestedStart)
  }
  const latestPoint = pointTimestamps.at(-1)
  if (Number.isFinite(requestedEnd) && (latestPoint === undefined || requestedEnd - latestPoint >= minimumSeparation)) {
    candidates.push(requestedEnd)
  }
  const uniqueCandidates = [...new Set(candidates)].sort((left, right) => left - right)
  if (uniqueCandidates.length <= maximum) return uniqueCandidates
  const stride = Math.ceil((uniqueCandidates.length - 1) / (maximum - 1))
  const ticks = uniqueCandidates.filter((_value, index) => index % stride === 0)
  const finalCandidate = uniqueCandidates.at(-1)
  if (finalCandidate !== undefined && ticks.at(-1) !== finalCandidate) ticks.push(finalCandidate)
  return ticks
}

export function chartAvailabilityMessage(
  points: HistoryPoint[],
  rangeStart: string | undefined,
  timezone: string,
): string | undefined {
  const requestedStart = Date.parse(rangeStart ?? '')
  if (!Number.isFinite(requestedStart)) return undefined
  const firstAvailable = points
    .filter((point) => !point.missing && Number.isFinite(Date.parse(point.start)))
    .sort((left, right) => Date.parse(left.start) - Date.parse(right.start))[0]
  if (!firstAvailable || Date.parse(firstAvailable.start) - requestedStart < 60_000) return undefined
  const beginning = zoned(firstAvailable.start, timezone, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  })
  return `Data begins ${beginning}`
}

export function chartIntervalLabel(start: string, end: string, timezone: string): string {
  const left = intervalParts(start, timezone)
  const right = intervalParts(end, timezone)
  const label = left.day === right.day
    ? left.period === right.period
      ? `${left.day}, ${left.clock}–${right.clock} ${right.period}`
      : `${left.day}, ${left.clock} ${left.period}–${right.clock} ${right.period}`
    : `${left.day}, ${left.clock} ${left.period}–${right.day}, ${right.clock} ${right.period}`
  return left.offset !== right.offset ? `${label} (${left.offset}–${right.offset})` : label
}

export function chartAxisValue(value: string | number, kind: 'power' | 'energy' | 'cost', currency: string): string {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '—'
  if (kind === 'power') return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(numeric)} W`
  if (kind === 'energy') return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 5 }).format(numeric)} kWh`
  return new Intl.NumberFormat(undefined, {
    style: 'currency', currency, minimumFractionDigits: 2, maximumFractionDigits: Math.abs(numeric) < .01 && numeric !== 0 ? 4 : 2,
  }).format(numeric)
}
