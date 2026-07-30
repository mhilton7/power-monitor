import { StatusDot } from '../../components/data-display/Surface'
import type { SensorSummary } from '../../types/models'
import { sensorMeasurementTime } from '../../utils/format'
import { SensorElectricalStrip } from './SensorElectricalStrip'

export function SensorHealthEntry({ sensor }: { sensor: SensorSummary }) {
  const stateLabel = sensorStateLabel(sensor.measurementFreshness)
  return (
    <article
      aria-label={`${sensor.name} sensor, ${stateLabel.toLowerCase()}`}
      className="sensor-health-row"
    >
      <header>
        <div>
          <StatusDot state={sensorDotState(sensor.measurementFreshness)} label={sensor.name} />
          <small>
            {sensor.latestMeasurementAt
              ? sensorMeasurementTime(
                  sensor.latestMeasurementAt,
                  sensor.measurementFreshness,
                )
              : sensorStateDetail(sensor.measurementFreshness)}
          </small>
        </div>
        <span className={`pill ${sensor.measurementFreshness}`}>
          {stateLabel}
        </span>
      </header>
      <SensorElectricalStrip sensor={sensor} />
    </article>
  )
}

function sensorDotState(
  state: SensorSummary['measurementFreshness'],
): 'live' | 'waiting' | 'attention' {
  if (state === 'live') return 'live'
  if (state === 'waiting' || state === 'stale') return 'waiting'
  return 'attention'
}

function sensorStateLabel(state: SensorSummary['measurementFreshness']): string {
  return {
    live: 'Online',
    waiting: 'Waiting',
    stale: 'Stale',
    offline: 'Offline',
    unavailable: 'Unavailable',
    invalid: 'Invalid',
    needs_attention: 'Needs attention',
  }[state]
}

function sensorStateDetail(state: SensorSummary['measurementFreshness']): string {
  return {
    live: 'Live measurement received',
    waiting: 'Waiting for first reading',
    stale: 'Last measurement is stale',
    offline: 'Sensor heartbeat is offline',
    unavailable: 'Meter data is unavailable',
    invalid: 'Latest measurement failed validation',
    needs_attention: 'Sensor is online but needs attention',
  }[state]
}
