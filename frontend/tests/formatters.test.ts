import { describe, expect, it } from 'vitest'
import {
  formatBillingPeriod,
  formatCurrency,
  formatDecimalDetail,
  formatEnergy,
  formatEnergyRate,
  formatPercentage,
  formatStructuredLabel,
  formatTierRange,
} from '../src/formatters'

describe('shared exact-value display formatters', () => {
  it('renders the deterministic 951 kWh tier fixture readably', () => {
    expect(formatCurrency('322.500000000')).toBe('$322.50')
    expect(formatEnergyRate('0.3391167192429022082018927445', { derived: true }))
      .toBe('$0.3391/kWh')
    expect(formatCurrency('173.7000000')).toBe('$173.70')
    expect(formatCurrency('148.8000000')).toBe('$148.80')
    expect(formatTierRange('0', '579')).toBe('0\u2013579 kWh')
    expect(formatEnergy('579.000')).toBe('579 kWh')
    expect(formatTierRange('579', null)).toBe('580 kWh and above')
    expect(formatEnergy('372')).toBe('372 kWh')
  })

  it('uses rate, energy, percentage, and period precision rules', () => {
    expect(formatEnergyRate('0.30')).toBe('$0.30/kWh')
    expect(formatEnergyRate('0.34000')).toBe('$0.34/kWh')
    expect(formatEnergyRate('0.3391167')).toBe('$0.33912/kWh')
    expect(formatEnergy('12.3456')).toBe('12.346 kWh')
    expect(formatPercentage('99.999')).toBe('100%')
    expect(formatBillingPeriod('2026-07-22T00:00:00-07:00', '2026-08-20T00:00:00-07:00'))
      .toBe('Jul 22, 2026 \u2013 Aug 20, 2026')
  })

  it('keeps exact strings available and generates labels from numeric bounds', () => {
    const exact = '0.3391167192429022082018927445'
    expect(formatDecimalDetail(exact)).toBe(exact)
    expect(formatTierRange('579.5', null)).toBe('579.5 kWh and above')
    expect(formatTierRange('0', '579.5')).toBe('0\u2013579.5 kWh')
    for (const output of [
      formatTierRange('0', '579'),
      formatTierRange('579', null),
      formatBillingPeriod('2026-07-22', '2026-08-20'),
    ]) {
      expect(output).not.toMatch(/\u00e2\u20ac|\u00ef\u00bf\u00bd|\ufffd/)
    }
  })

  it('formats structured labels without crashing on incomplete API data', () => {
    expect(formatStructuredLabel('administrator_confirmed')).toBe('administrator confirmed')
    expect(formatStructuredLabel('rate-plan.threshold_basis')).toBe('rate plan · threshold basis')
    expect(formatStructuredLabel(undefined)).toBe('Unavailable')
    expect(formatStructuredLabel('')).toBe('Unavailable')
    expect(formatStructuredLabel(null, 'Not reported')).toBe('Not reported')
  })
})
