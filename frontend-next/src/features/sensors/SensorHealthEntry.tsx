import { StatusDot } from '../../components/data-display/Surface'
import { ElapsedTime } from '../../components/data-display/ElapsedTime'
import type { SensorSummary } from '../../types/models'
import { sensorMeasurementTime } from '../../utils/format'
import { SensorElectricalStrip } from './SensorElectricalStrip'

export function SensorHealthEntry({
  sensor,
  serverNow,
}: {
  sensor: SensorSummary
  serverNow?: string
}) {
  const stateLabel = sensorOperationalLabel(sensor)
  return (
    <article
      aria-label={`${sensor.name} sensor, ${stateLabel.toLowerCase()}`}
      className="sensor-health-row"
    >
      <header>
        <div>
          <StatusDot state={sensorDotState(sensor.measurementFreshness)} label={sensor.name} />
          <small>
            {sensor.measurementReceivedAt
              ? <>
                  Received <ElapsedTime
                    timestamp={sensor.measurementReceivedAt}
                    serverNow={serverNow}
                    serverReceipt
                  />
                </>
              : sensor.latestMeasurementAt
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
      <dl
        aria-label={`${sensor.name} hardware health`}
        className="sensor-hardware-strip"
        role="group"
      >
        <div data-state={subsystemState(sensor.pzemHealthy)}>
          <dt>PZEM meter</dt>
          <dd>{subsystemLabel(sensor.pzemHealthy, sensor.pzemStatus)}</dd>
        </div>
        <div data-state={subsystemState(sensor.storageHealthy)}>
          <dt>microSD</dt>
          <dd>{subsystemLabel(sensor.storageHealthy, sensor.storageStatus)}</dd>
        </div>
        <div>
          <dt>Last heartbeat</dt>
          <dd>
            {sensor.heartbeatReceivedAt
              ? <ElapsedTime
                  timestamp={sensor.heartbeatReceivedAt}
                  serverNow={serverNow}
                  serverReceipt
                />
              : 'No signed evidence'}
          </dd>
        </div>
      </dl>
      {sensor.online && sensor.previousOutageReason && (
        <p className="sensor-previous-outage">
          <strong>Previous outage:</strong> {sensor.previousOutageReason}
        </p>
      )}
    </article>
  )
}

function subsystemState(value?: boolean): 'healthy' | 'fault' | 'unknown' {
  if (value === true) return 'healthy'
  if (value === false) return 'fault'
  return 'unknown'
}

function subsystemLabel(value?: boolean, status?: string): string {
  if (status) {
    return status
      .split('_')
      .map((part, index) => index === 0
        ? `${part.charAt(0).toUpperCase()}${part.slice(1)}`
        : part)
      .join(' ')
  }
  if (value === true) return 'Healthy'
  if (value === false) return 'Unavailable'
  return 'No signed evidence'
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

function sensorOperationalLabel(sensor: SensorSummary): string {
  if (sensor.heartbeatFreshness !== 'online') {
    return sensorStateLabel(sensor.measurementFreshness)
  }
  if (sensor.deviceStatus === 'online_storage_reconciling') {
    return 'Online · Storage reconciling'
  }
  if (
    sensor.deviceStatus === 'online_storage_degraded'
    || sensor.deviceStatus === 'api_healthy_storage_failed'
  ) {
    return 'Online · Storage degraded'
  }
  return sensorStateLabel(sensor.measurementFreshness)
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
