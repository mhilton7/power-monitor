import { percentage } from '../../utils/format'

export function coverageSummary(value: string | number | null | undefined): string {
  const parsed = Number(value)
  if (
    value === null
    || value === undefined
    || value === ''
    || !Number.isFinite(parsed)
    || parsed < 0
    || parsed > 100
  ) {
    return 'Stored history is not available yet.'
  }
  if (parsed >= 100) {
    return 'All expected sensor readings are stored for this period.'
  }
  return `${percentage(value)} of the expected sensor readings are stored for this period.`
}

export function CoverageExplanation({
  value,
  combined = false,
}: {
  value: string | number | null | undefined
  combined?: boolean
}) {
  return (
    <details className="coverage-explanation">
      <summary>
        <strong>Reading coverage: {percentage(value)}</strong>
        <span>What this means</span>
      </summary>
      <div>
        <p>{coverageSummary(value)}</p>
        <p>
          Coverage measures saved history, not whether a sensor is online right now.
          Missing readings remain visible gaps and can make energy and cost totals incomplete.
        </p>
        {combined && (
          <p>For a combined total, every required sensor must provide a usable reading for the interval to be complete.</p>
        )}
      </div>
    </details>
  )
}
