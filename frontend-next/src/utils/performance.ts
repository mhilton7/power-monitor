export const HISTORY_PERFORMANCE_MARKS = {
  jsonParse: 'power-monitor.history.json-parse',
  adaptation: 'power-monitor.history.adaptation',
  timestampParse: 'power-monitor.history.timestamp-parse',
  chartPreparation: 'power-monitor.history.chart-preparation',
} as const

function enabled(): boolean {
  if (typeof performance === 'undefined' || typeof window === 'undefined') return false
  return (window as typeof window & { __pmCollectHistoryPerformance?: boolean })
    .__pmCollectHistoryPerformance === true
}

function record(name: string, start: number): void {
  try {
    performance.measure(name, { start, end: performance.now() })
  } catch {
    // User Timing is diagnostic only. Unsupported or quota-limited browser
    // implementations must never affect a real History request or render.
  }
}

export function measureSync<T>(name: string, operation: () => T): T {
  if (!enabled()) return operation()
  const start = performance.now()
  try {
    return operation()
  } finally {
    record(name, start)
  }
}

export async function measureAsync<T>(name: string, operation: () => Promise<T>): Promise<T> {
  if (!enabled()) return operation()
  const start = performance.now()
  try {
    return await operation()
  } finally {
    record(name, start)
  }
}
