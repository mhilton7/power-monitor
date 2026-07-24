export type DecimalDisplayValue = string | number | null | undefined

interface FormatOptions {
  locale?: string
  currency?: string
}

const numeric = (value: DecimalDisplayValue): number | undefined => {
  if (value === null || value === undefined || value === '') return undefined
  const result = Number(value)
  return Number.isFinite(result) ? result : undefined
}

const formatNumeric = (
  value: DecimalDisplayValue,
  minimumFractionDigits: number,
  maximumFractionDigits: number,
  locale = 'en-US',
) => {
  const parsed = numeric(value)
  if (parsed === undefined) return 'Unavailable'
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits,
    maximumFractionDigits,
    useGrouping: true,
  }).format(parsed)
}

export function formatCurrency(
  value: DecimalDisplayValue,
  { locale = 'en-US', currency = 'USD' }: FormatOptions = {},
) {
  const parsed = numeric(value)
  if (parsed === undefined) return 'Unavailable'
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(parsed)
}

export function formatEnergyRate(
  value: DecimalDisplayValue,
  options: FormatOptions & { derived?: boolean } = {},
) {
  const { locale = 'en-US', currency = 'USD', derived = false } = options
  const parsed = numeric(value)
  if (parsed === undefined) return 'Unavailable'
  const formatted = new Intl.NumberFormat(locale, {
    style: 'currency',
    currency,
    minimumFractionDigits: derived ? 4 : 2,
    maximumFractionDigits: derived ? 4 : 5,
  }).format(parsed)
  return `${formatted}/kWh`
}

export function formatEnergy(
  value: DecimalDisplayValue,
  { locale = 'en-US' }: Pick<FormatOptions, 'locale'> = {},
) {
  const formatted = formatNumeric(value, 0, 3, locale)
  return formatted === 'Unavailable' ? formatted : `${formatted} kWh`
}

export function formatPercentage(
  value: DecimalDisplayValue,
  { locale = 'en-US' }: Pick<FormatOptions, 'locale'> = {},
) {
  const formatted = formatNumeric(value, 0, 2, locale)
  return formatted === 'Unavailable' ? formatted : `${formatted}%`
}

export function formatTierRange(
  lowerBoundKwh: DecimalDisplayValue,
  upperBoundExclusiveKwh: DecimalDisplayValue,
  { locale = 'en-US' }: Pick<FormatOptions, 'locale'> = {},
) {
  const lower = numeric(lowerBoundKwh)
  if (lower === undefined) return 'Unavailable'
  const upper = numeric(upperBoundExclusiveKwh)
  if (upper === undefined) {
    const displayLower = Number.isInteger(lower) && lower > 0 ? lower + 1 : lower
    return `${formatNumeric(displayLower, 0, 3, locale)} kWh and above`
  }
  return `${formatNumeric(lower, 0, 3, locale)}\u2013${formatNumeric(upper, 0, 3, locale)} kWh`
}

export function formatBillingPeriod(
  start: string | Date | null | undefined,
  end: string | Date | null | undefined,
  { locale = 'en-US' }: Pick<FormatOptions, 'locale'> = {},
) {
  if (!start || !end) return 'Unavailable'
  const startDate = start instanceof Date ? start : new Date(start)
  const endDate = end instanceof Date ? end : new Date(end)
  if (!Number.isFinite(startDate.getTime()) || !Number.isFinite(endDate.getTime())) return 'Unavailable'
  const formatter = new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
  return `${formatter.format(startDate)} \u2013 ${formatter.format(endDate)}`
}

export function formatDecimalDetail(value: DecimalDisplayValue) {
  if (value === null || value === undefined || value === '') return 'Unavailable'
  return String(value)
}

export function formatStructuredLabel(
  value: unknown,
  fallback = 'Unavailable',
) {
  if (typeof value !== 'string') return fallback
  const label = value.trim()
  if (!label) return fallback
  return label
    .replaceAll('.', ' · ')
    .replaceAll('_', ' ')
    .replaceAll('-', ' ')
    .replace(/\s+/g, ' ')
}
