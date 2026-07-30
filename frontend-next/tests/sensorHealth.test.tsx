import { readFileSync } from 'node:fs'
import path from 'node:path'
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SensorElectricalStrip } from '../src/features/sensors/SensorElectricalStrip'
import { SensorHealthEntry } from '../src/features/sensors/SensorHealthEntry'
import type { SensorSummary } from '../src/types/models'

function summary(
  overrides: Partial<SensorSummary> = {},
): SensorSummary {
  return {
    id: 'sensor-1',
    name: 'Indoor-AC',
    state: 'live',
    deviceStatus: 'online_synchronized',
    online: true,
    currentPowerW: '1',
    voltageVolts: '116.3',
    currentAmps: '0',
    frequencyHz: '60',
    powerFactor: '0',
    latestMeasurementAt: '2026-07-30T12:00:00Z',
    measurementSource: 'heartbeat_live',
    measurementFreshness: 'live',
    invalidMetrics: [],
    monitoredCircuit: 'Whole Home',
    backlog: 0,
    ctRatingAmps: '100',
    measurementRole: 'energy_only',
    ...overrides,
  }
}

describe('Sensor Health compact electrical measurements', () => {
  it('renders all five values in one shared strip and preserves valid zeroes', () => {
    const { container } = render(<SensorElectricalStrip sensor={summary()} />)
    const strip = screen.getByRole('group', {
      name: 'Indoor-AC electrical measurements',
    })

    expect(strip).toHaveClass('sensor-electrical-strip')
    expect(strip.querySelectorAll('.sensor-electrical-metric')).toHaveLength(5)
    expect(container.querySelector('.sensor-electrical-grid')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Power, 1 W')).toHaveTextContent('1 W')
    expect(screen.getByLabelText('Voltage, 116.3 V')).toHaveTextContent('116.3 V')
    expect(screen.getByLabelText('Current, 0.00 A')).toHaveTextContent('0.00 A')
    expect(screen.getByLabelText('Frequency, 60.0 Hz')).toHaveTextContent('60.0 Hz')
    expect(screen.getByLabelText('Power factor, 0.00')).toHaveTextContent('0.00')
    expect(within(strip).getByText('PF')).toHaveAttribute('aria-hidden', 'true')
  })

  it('uses a compact dash and an accessible explanation for invalid or missing values', () => {
    render(<SensorElectricalStrip sensor={summary({
      currentPowerW: undefined,
      powerFactor: undefined,
      invalidMetrics: ['power_watts'],
      measurementFreshness: 'invalid',
    })} />)

    expect(screen.getByLabelText('Power measurement invalid')).toHaveTextContent('—')
    expect(screen.getByLabelText('Power factor measurement unavailable')).toHaveTextContent('—')
    expect(screen.queryByText('Invalid reading')).not.toBeInTheDocument()
    expect(screen.queryByText('Not available')).not.toBeInTheDocument()
  })

  it.each([
    ['live', 'Online'],
    ['waiting', 'Waiting'],
    ['stale', 'Stale'],
    ['offline', 'Offline'],
    ['invalid', 'Invalid'],
    ['unavailable', 'Unavailable'],
    ['needs_attention', 'Needs attention'],
  ] as const)('keeps the %s sensor-level state visible', (state, label) => {
    render(<SensorHealthEntry sensor={summary({
      id: `sensor-${state}`,
      name: `Sensor ${state}`,
      latestMeasurementAt: undefined,
      measurementFreshness: state,
    })} />)

    const entry = screen.getByRole('article', {
      name: `Sensor ${state} sensor, ${label.toLowerCase()}`,
    })
    expect(within(entry).getByText(label)).toBeVisible()
    expect(within(entry).getByRole('group', {
      name: `Sensor ${state} electrical measurements`,
    })).toBeVisible()
  })

  it('does not restore the removed per-metric box styling', () => {
    const css = readFileSync(
      path.join(process.cwd(), 'src', 'theme', 'components.css'),
      'utf8',
    )
    expect(css).not.toContain('.sensor-electrical-grid')
    const metricRule = css.match(/\.sensor-electrical-metric\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(metricRule).not.toMatch(/\bborder\s*:/)
    expect(metricRule).not.toMatch(/\bbackground\s*:/)
    expect(metricRule).not.toMatch(/\bborder-radius\s*:/)
    expect(metricRule).not.toMatch(/\bpadding\s*:/)
    expect(css).toMatch(
      /\.sensor-electrical-strip\s*\{[^}]*display:\s*flex;[^}]*flex-wrap:\s*wrap;/,
    )
  })
})
