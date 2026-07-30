import { useState } from 'react'
import { useSecondClock } from '../../state/SecondClockContext'
import { elapsedSince } from '../../utils/format'

export function ElapsedTime({
  timestamp,
  serverNow,
}: {
  timestamp?: string
  serverNow?: string
}) {
  return (
    <ElapsedTimeValue
      key={`${timestamp ?? 'missing'}|${serverNow ?? 'local'}`}
      timestamp={timestamp}
      serverNow={serverNow}
    />
  )
}

function ElapsedTimeValue({
  timestamp,
  serverNow,
}: {
  timestamp?: string
  serverNow?: string
}) {
  const nowMs = useSecondClock()
  const [baseline] = useState(() => ({
    serverNow,
    observedAtMs: Date.now(),
  }))
  return elapsedSince(
    timestamp,
    nowMs,
    baseline.serverNow,
    baseline.observedAtMs,
  )
}
