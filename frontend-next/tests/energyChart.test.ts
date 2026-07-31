import { describe, expect, it } from 'vitest'
import { energyChartTooltipLines } from '../src/components/charts/EnergyChart'
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
})
