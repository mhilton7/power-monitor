import { describe, expect, it } from 'vitest'
import { energyChartSeries, energyChartTooltipLines } from '../src/components/charts/EnergyChart'
import { chartAvailabilityMessage, chartAxisValue, chartIntervalLabel, chartTickLabel, chartTickTimestamps, colorWithAlpha } from '../src/components/charts/chartUtils'
import { chartColorContrast, normalizeChartColor } from '../src/state/AppearanceContext'
import type { HistoryPoint } from '../src/types/models'

describe('energy chart tooltip formatting', () => {
  it('normalizes exact rates and percentages only at the display boundary', () => {
    const point: HistoryPoint = {
      start: '2026-07-30T18:00:00Z',
      end: '2026-07-30T19:00:00Z',
      label: 'Jul 30, 6 PM',
      energyKwh: '0.00025',
      rate: '0.3086299953577010201376727817',
      period: 'Tier 1',
      tier: 'Tier 1',
      coveragePercent: '60.3444444444444',
      missing: false,
    }

    expect(energyChartTooltipLines(point, 'USD')).toEqual([
      'Period: Tier 1',
      'Tier: Tier 1',
      'Rate: $0.30863/kWh',
      'Coverage: 60.34%',
    ])
  })

  it('does not fabricate optional rate context', () => {
    const point: HistoryPoint = {
      start: '2026-07-30T18:00:00Z',
      end: '2026-07-30T19:00:00Z',
      label: 'Jul 30, 6 PM',
      coveragePercent: '100',
      missing: false,
    }

    expect(energyChartTooltipLines(point, 'USD')).toEqual([
      'Coverage: 100%',
    ])
  })

  it('represents missing intervals as timestamped gaps instead of null chart data', () => {
    const points: HistoryPoint[] = [
      { start: '2026-07-30T18:00:00Z', end: '2026-07-30T18:15:00Z', label: '11:00 AM', energyKwh: '0.25', coveragePercent: '100', missing: false },
      { start: '2026-07-30T18:15:00Z', end: '2026-07-30T18:30:00Z', label: '11:15 AM', coveragePercent: '0', missing: true },
      { start: '2026-07-30T18:30:00Z', end: '2026-07-30T18:45:00Z', label: '11:30 AM', energyKwh: '0.30', coveragePercent: '100', missing: false },
    ]

    const series = energyChartSeries(points, 'energyKwh')

    expect(series).toHaveLength(3)
    expect(series).not.toContain(null)
    expect(series.every((point) => Number.isFinite(point.x))).toBe(true)
    expect(series[0]?.y).toBe(0.25)
    expect(Number.isNaN(series[1]?.y)).toBe(true)
    expect(series[2]?.y).toBe(0.30)
  })

  it('formats exact intervals and bucket-aware ticks in the account timezone', () => {
    expect(chartIntervalLabel('2026-07-31T15:15:00Z', '2026-07-31T15:30:00Z', 'America/Los_Angeles')).toBe('Jul 31, 8:15–8:30 AM')
    expect(chartTickLabel(Date.parse('2026-07-31T15:15:00Z'), '15m', 'America/Los_Angeles')).toBe('8:15 AM')
    expect(chartTickLabel(Date.parse('2026-07-31T15:00:00Z'), '1h', 'America/Los_Angeles')).toContain('Jul 31')
    expect(chartTickLabel(Date.parse('2026-07-31T15:05:00Z'), '5m', 'America/Los_Angeles')).toBe('8:05 AM')
    expect(chartTickLabel(Date.parse('2026-07-31T15:00:00Z'), '1d', 'America/Los_Angeles')).toBe('Jul 31')
  })

  it('validates persisted colors and derives alpha without fragile color parsing', () => {
    expect(normalizeChartColor('#c9a7ff', '#78DFBF')).toBe('#C9A7FF')
    expect(normalizeChartColor('not-a-color', '#78DFBF')).toBe('#78DFBF')
    expect(colorWithAlpha('#C9A7FF', .08)).toBe('rgba(201, 167, 255, 0.08)')
    expect(chartColorContrast('#000000', '#FFFFFF')).toBeCloseTo(21)
  })

  it('distinguishes repeated fall-back intervals by UTC offset', () => {
    expect(chartIntervalLabel('2026-11-01T08:30:00Z', '2026-11-01T09:30:00Z', 'America/Los_Angeles')).toContain('GMT-7–GMT-8')
  })

  it('uses real bucket timestamps and explains a leading sparse range', () => {
    const points: HistoryPoint[] = [
      { start: '2026-07-31T15:15:00Z', end: '2026-07-31T15:30:00Z', label: '8:15 AM', energyKwh: '0.001', coveragePercent: '100', missing: false },
      { start: '2026-07-31T15:30:00Z', end: '2026-07-31T15:45:00Z', label: '8:30 AM', energyKwh: '0.001', coveragePercent: '100', missing: false },
    ]
    expect(chartTickTimestamps(points, '2026-07-31T14:00:00Z', '2026-07-31T16:00:00Z')).toEqual([
      Date.parse('2026-07-31T14:00:00Z'), Date.parse('2026-07-31T15:15:00Z'), Date.parse('2026-07-31T15:30:00Z'), Date.parse('2026-07-31T16:00:00Z'),
    ])
    expect(chartAvailabilityMessage(points, '2026-07-31T14:00:00Z', 'America/Los_Angeles')).toBe(
      'Readings are available beginning Jul 31, 8:15 AM. Earlier intervals in this range contain no synchronized data.',
    )
    const firstPoint = points[0]
    expect(firstPoint).toBeDefined()
    if (!firstPoint) return
    expect(chartTickTimestamps(
      [firstPoint],
      '2026-07-30T14:00:00Z',
      '2026-07-31T16:00:00Z',
    )).toEqual([
      Date.parse('2026-07-30T14:00:00Z'),
      Date.parse('2026-07-31T15:15:00Z'),
    ])
  })

  it('keeps useful precision for small interval energy and cost axis values', () => {
    expect(chartAxisValue('0.00025', 'energy', 'USD')).toBe('0.00025 kWh')
    expect(chartAxisValue('0.0006', 'cost', 'USD')).toBe('$0.0006')
  })
})
