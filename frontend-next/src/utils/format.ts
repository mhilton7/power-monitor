export function number(value: string | number | undefined, digits = 1): string {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 'Unavailable'
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(parsed)
}

export function power(value: string | number | undefined): string {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '—'
  if (Math.abs(parsed) >= 1000) return `${number(parsed / 1000, 2)} kW`
  return `${number(parsed, 0)} W`
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
