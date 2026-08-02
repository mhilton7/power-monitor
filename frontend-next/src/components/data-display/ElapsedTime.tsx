import { useState } from 'react'
import { useSecondClock } from '../../state/SecondClockContext'
import { elapsedSince } from '../../utils/format'

export function ElapsedTime({
  timestamp,
  serverNow,
  serverReceipt = false,
}: {
  timestamp?: string
  serverNow?: string
  serverReceipt?: boolean
}) {
  const effectiveServerNow = serverReceipt
    ? latestServerTimestamp(serverNow, timestamp)
    : serverNow
  return (
    <ElapsedTimeValue
      key={`${timestamp ?? 'missing'}|${effectiveServerNow ?? 'local'}`}
      timestamp={timestamp}
      serverNow={effectiveServerNow}
    />
  )
}

function latestServerTimestamp(
  serverNow: string | undefined,
  serverReceipt: string | undefined,
): string | undefined {
  if (!serverReceipt) return serverNow
  const receiptMs = new Date(serverReceipt).getTime()
  if (!Number.isFinite(receiptMs)) return serverNow
  if (!serverNow) return serverReceipt
  const serverNowMs = new Date(serverNow).getTime()
  if (!Number.isFinite(serverNowMs)) return serverReceipt
  return receiptMs > serverNowMs ? serverReceipt : serverNow
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
