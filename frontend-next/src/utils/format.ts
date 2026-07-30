export function number(value: string | number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || value === '') return 'Unavailable'
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 'Unavailable'
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(parsed)
}

export function power(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '—'
  const normalized = Object.is(parsed, -0) ? 0 : parsed
  if (Math.abs(normalized) >= 1000) return `${number(normalized / 1000, 2)} kW`
  return `${number(normalized, 2)} W`
}

export function voltage(value: string | number | undefined): string {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '—'
  return `${fixedNumber(Object.is(parsed, -0) ? 0 : parsed, 1)} V`
}

export function current(value: string | number | undefined): string {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '—'
  return `${fixedNumber(Object.is(parsed, -0) ? 0 : parsed, 2)} A`
}

export function frequency(value: string | number | undefined): string {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '—'
  return `${fixedNumber(Object.is(parsed, -0) ? 0 : parsed, 1)} Hz`
}

export function powerFactor(value: string | number | undefined): string {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '—'
  return fixedNumber(Object.is(parsed, -0) ? 0 : parsed, 2)
}

function fixedNumber(value: number, digits: number): string {
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value)
}

export function energy(value: string | number | undefined): string {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${number(parsed, parsed < 10 ? 2 : 1)} kWh` : 'Unavailable'
}

export function money(value: string | number | undefined, currency = 'USD'): string {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 'Unavailable'
  return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(parsed)
}

export function rate(value: string | number | undefined, currency = 'USD'): string {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 'Unavailable'
  return `${new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency,
    minimumFractionDigits: 3,
    maximumFractionDigits: 5,
  }).format(parsed)}/kWh`
}

export function dateTime(value: string | undefined): string {
  if (!value) return 'Not yet'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime())
    ? 'Unavailable'
    : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(parsed)
}

export function dateRange(start: string | undefined, end: string | undefined): string {
  if (!start || !end) return 'Not available'
  return `${new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(new Date(start))} – ${new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(end))}`
}

export function relativeTime(value: string | undefined): string {
  if (!value) return 'Waiting for data'
  const seconds = Math.round((new Date(value).getTime() - Date.now()) / 1000)
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  if (Math.abs(seconds) < 60) return formatter.format(seconds, 'second')
  const minutes = Math.round(seconds / 60)
  if (Math.abs(minutes) < 60) return formatter.format(minutes, 'minute')
  return formatter.format(Math.round(minutes / 60), 'hour')
}

const FUTURE_TIMESTAMP_TOLERANCE_MS = 5_000
const VERY_OLD_TIMESTAMP_MS = 7 * 24 * 60 * 60 * 1_000

export function elapsedSince(
  value: string | undefined,
  nowMs: number,
  serverNow?: string,
  observedAtMs = nowMs,
): string {
  if (!value) return 'Waiting for data'
  const timestampMs = new Date(value).getTime()
  const serverNowMs = serverNow ? new Date(serverNow).getTime() : Number.NaN
  const effectiveNow = Number.isFinite(serverNowMs)
    ? serverNowMs + Math.max(0, nowMs - observedAtMs)
    : nowMs
  if (!Number.isFinite(timestampMs) || timestampMs - effectiveNow > FUTURE_TIMESTAMP_TOLERANCE_MS) {
    return 'Invalid timestamp'
  }
  const elapsedMs = Math.max(0, effectiveNow - timestampMs)
  if (elapsedMs >= VERY_OLD_TIMESTAMP_MS) {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(timestampMs))
  }
  const seconds = Math.floor(elapsedMs / 1_000)
  if (seconds < 1) return 'Just now'
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ${seconds % 60}s ago`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m ago`
}

export function percentage(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '—'
  const epsilon = 1e-9
  if (parsed < 0 || parsed > 100 + epsilon) return '—'
  const normalized = parsed > 100 ? 100 : Object.is(parsed, -0) ? 0 : parsed
  return `${new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(normalized)}%`
}

export function sensorMeasurementTime(
  value: string | undefined,
  freshness: string,
  now = Date.now(),
): string {
  if (!value) {
    return freshness === 'waiting'
      ? 'Waiting for first reading'
      : 'Measurement time unavailable'
  }
  const measuredAt = new Date(value)
  const measuredAtMs = measuredAt.getTime()
  if (!Number.isFinite(measuredAtMs) || measuredAtMs - now > 60_000) {
    return 'Measurement time unavailable'
  }
  const elapsedMs = Math.max(0, now - measuredAtMs)
  const prefix = freshness === 'live' ? 'Updated' : 'Last reading'
  if (elapsedMs < 60_000) return `${prefix} just now`
  const minutes = Math.round(elapsedMs / 60_000)
  if (minutes < 60) return `${prefix} ${minutes} ${minutes === 1 ? 'minute' : 'minutes'} ago`
  const hours = Math.round(elapsedMs / 3_600_000)
  if (hours < 24) return `${prefix} ${hours} ${hours === 1 ? 'hour' : 'hours'} ago`
  if (hours < 48) return 'Last reading yesterday'
  return `Last reading ${new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(measuredAt)}`
}

export function statusLabel(value: string): string {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

export function fileSize(value: number | undefined): string {
  if (value === undefined || !Number.isFinite(value)) return 'Size unavailable'
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${number(value / 1024, 1)} KB`
  if (value < 1024 ** 3) return `${number(value / 1024 ** 2, 1)} MB`
  return `${number(value / 1024 ** 3, 2)} GB`
}
