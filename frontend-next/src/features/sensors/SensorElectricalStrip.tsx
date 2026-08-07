import type { SensorSummary } from '../../types/models'
import {
  current,
  energy,
  frequency,
  power,
  powerFactor,
  voltage,
} from '../../utils/format'

interface ElectricalMetric {
  key: string
  invalidKey: string
  label: string
  compactLabel: string
  rawValue?: string
  format: (value: string | undefined) => string
}

export function SensorElectricalStrip({ sensor }: { sensor: SensorSummary }) {
  const metrics: ElectricalMetric[] = [
    {
      key: 'power',
      invalidKey: 'power_watts',
      label: 'Power',
      compactLabel: 'Power',
      rawValue: sensor.currentPowerW,
      format: power,
    },
    {
      key: 'voltage',
      invalidKey: 'voltage_volts',
      label: 'Voltage',
      compactLabel: 'Voltage',
      rawValue: sensor.voltageVolts,
      format: voltage,
    },
    {
      key: 'current',
      invalidKey: 'current_amps',
      label: 'Current',
      compactLabel: 'Current',
      rawValue: sensor.currentAmps,
      format: current,
    },
    {
      key: 'frequency',
      invalidKey: 'frequency_hz',
      label: 'Frequency',
      compactLabel: 'Frequency',
      rawValue: sensor.frequencyHz,
      format: frequency,
    },
    {
      key: 'power-factor',
      invalidKey: 'power_factor',
      label: 'Power factor',
      compactLabel: 'PF',
      rawValue: sensor.powerFactor,
      format: powerFactor,
    },
    {
      key: 'energy',
      invalidKey: 'energy_wh',
      label: 'Meter energy',
      compactLabel: 'Energy',
      rawValue: sensor.currentEnergyWh,
      format: (value) => energy(value === undefined ? undefined : Number(value) / 1000),
    },
  ]

  return (
    <dl
      aria-label={`${sensor.name} electrical measurements`}
      className="sensor-electrical-strip"
      role="group"
    >
      {metrics.map((metric) => {
        const invalid = sensor.invalidMetrics.includes(metric.invalidKey)
        const missing = metric.rawValue === undefined
        const value = invalid || missing ? '—' : metric.format(metric.rawValue)
        const state = invalid ? 'invalid' : missing ? 'unavailable' : 'valid'
        const accessibleValue = invalid
          ? `${metric.label} measurement invalid`
          : missing
            ? `${metric.label} measurement unavailable`
            : `${metric.label}, ${value}`
        return (
          <div
            className="sensor-electrical-metric"
            data-metric={metric.key}
            data-state={state}
            key={metric.key}
          >
            <dt>
              {metric.compactLabel === metric.label
                ? metric.label
                : (
                    <>
                      <span aria-hidden="true">{metric.compactLabel}</span>
                      <span className="sr-only">{metric.label}</span>
                    </>
                  )}
            </dt>
            <dd
              aria-label={accessibleValue}
              className={state}
              title={invalid || missing ? accessibleValue : undefined}
            >
              {value}
            </dd>
          </div>
        )
      })}
    </dl>
  )
}
