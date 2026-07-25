import { useMutation, useQuery } from '@tanstack/react-query'
import { AlertTriangle, CalendarRange, Download, Gauge, Layers3 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { adaptHistory } from '../../api/adapters'
import { download, errorMessage, json, request, saveBlob } from '../../api/client'
import { EnergyChart } from '../../components/charts/EnergyChart'
import { Metric, Surface } from '../../components/data-display/Surface'
import { EmptyState, ErrorState, InlineNotice, LoadingState } from '../../components/feedback/States'
import { Page, PageHeader, SegmentedControl, StatGrid } from '../../components/layout/Layout'
import { historyPayload } from '../../features/history/historyQuery'
import { useLiveHome } from '../../state/LiveHomeContext'
import { useSingleHome } from '../../state/SingleHomeContext'
import type { HistoryFilters, HistoryMetric, HistoryRange, HistoryScope } from '../../types/models'
import { energy, money, number, power, rate } from '../../utils/format'

const ranges: Array<{ value: HistoryRange; label: string }> = [
  { value: 'today', label: 'Today' },
  { value: '7d', label: '7 days' },
  { value: '30d', label: '30 days' },
  { value: 'cycle', label: 'Billing cycle' },
  { value: 'custom', label: 'Custom' },
]

const metrics: Array<{ value: HistoryMetric; label: string }> = [
  { value: 'power', label: 'Power' },
  { value: 'energy', label: 'Energy' },
  { value: 'cost', label: 'Cost' },
  { value: 'energy_cost', label: 'Energy + cost' },
]

export function HistoryPage() {
  const { resolution } = useSingleHome()
  const { sensors, cycle } = useLiveHome()
  const home = resolution?.state === 'ready' ? resolution.home : undefined
  const [filters, setFilters] = useState<HistoryFilters>({
    range: '7d',
    metric: 'energy_cost',
    scope: 'home',
  })
  const payload = useMemo(
    () => home ? historyPayload(filters, home, cycle?.startsAt, cycle?.endsAt) : undefined,
    [cycle?.endsAt, cycle?.startsAt, filters, home],
  )
  const validScope = filters.scope === 'home' || Boolean(filters.sensorId)
  const history = useQuery({
    queryKey: ['history', payload],
    queryFn: () => request('/api/v1/history/query', json('POST', payload), adaptHistory),
    enabled: Boolean(payload && validScope),
    placeholderData: (previous) => previous,
  })
  const exportHistory = useMutation({
    mutationFn: async () => {
      const blob = await download('/api/v1/history/export', json('POST', payload))
      saveBlob(blob, `power-monitor-${filters.range}-history.csv`)
    },
  })

  if (!home) return <ErrorState error={new Error('The default home is unavailable.')} />

  return (
    <Page className="history-page">
      <PageHeader
        title="History"
        description="See how your home used energy and what it cost over time."
        action={<button type="button" className="button secondary" disabled={!payload || exportHistory.isPending} onClick={() => { exportHistory.mutate(); }}>
          <Download size={17} /> {exportHistory.isPending ? 'Preparing…' : 'Export'}
        </button>}
      />
      {exportHistory.error && <InlineNotice tone="danger">{errorMessage(exportHistory.error)}</InlineNotice>}

      <Surface className="history-controls-surface">
        <SegmentedControl label="History range" value={filters.range} items={ranges} onChange={(range) => { setFilters((current) => ({ ...current, range })); }} />
        <div className="history-filter-row">
          <label>
            <span><Gauge size={15} /> Metric</span>
            <select value={filters.metric} onChange={(event) => { setFilters((current) => ({ ...current, metric: event.target.value as HistoryMetric })); }}>
              {metrics.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
          </label>
          <label>
            <span><Layers3 size={15} /> Scope</span>
            <select value={filters.scope} onChange={(event) => { setFilters((current) => ({ ...current, scope: event.target.value as HistoryScope, sensorId: undefined })); }}>
              <option value="home">Whole Home</option>
              <option value="sensor">Individual sensor</option>
            </select>
          </label>
          {filters.scope === 'sensor' && (
            <label>
              <span>Sensor</span>
              <select value={filters.sensorId ?? ''} onChange={(event) => { setFilters((current) => ({ ...current, sensorId: event.target.value })); }}>
                <option value="">Choose a sensor</option>
                {sensors.map((sensor) => <option key={sensor.id} value={sensor.id}>{sensor.name}</option>)}
              </select>
            </label>
          )}
          {filters.range === 'custom' && (
            <>
              <label><span><CalendarRange size={15} /> Start</span><input type="datetime-local" value={filters.customStart ?? ''} onChange={(event) => { setFilters((current) => ({ ...current, customStart: event.target.value })); }} /></label>
              <label><span>End</span><input type="datetime-local" value={filters.customEnd ?? ''} onChange={(event) => { setFilters((current) => ({ ...current, customEnd: event.target.value })); }} /></label>
            </>
          )}
        </div>
      </Surface>

      {!validScope ? (
        <Surface><EmptyState title="Choose a sensor" message="Select one sensor to see its individual readings." /></Surface>
      ) : history.isLoading ? (
        <Surface><LoadingState label="Calculating history and exact interval costs…" /></Surface>
      ) : history.error && !history.data ? (
        <Surface><ErrorState error={history.error} retry={() => void history.refetch()} /></Surface>
      ) : history.data ? (
        <>
          {Number(history.data.coveragePercent) < 99 && (
            <InlineNotice tone="warning">
              <AlertTriangle size={17} />
              Coverage is {number(history.data.coveragePercent, 1)}%. Missing readings remain gaps and are not replaced with zero.
            </InlineNotice>
          )}
          {history.data.warnings.map((warning) => <InlineNotice key={warning} tone="warning">{warning}</InlineNotice>)}
          <Surface title={history.data.title} subtitle={`${filters.scope === 'home' ? 'Whole Home' : 'Individual sensor'} · ${history.data.contributingSensors} contributing sensor${history.data.contributingSensors === 1 ? '' : 's'}`}>
            <StatGrid className="history-summary">
              <Metric label="Energy" value={energy(history.data.energyKwh)} identity="history.energy" />
              <Metric label="Estimated cost" value={money(history.data.cost, home.currency)} identity="history.cost" detail="Interval energy charges" />
              <Metric label="Blended rate" value={rate(history.data.blendedRate, home.currency)} identity="history.blended_rate" />
              <Metric label="Peak power" value={power(history.data.peakPowerW)} identity="history.peak_power" />
              <Metric label="Coverage" value={`${number(history.data.coveragePercent, 1)}%`} identity="data.coverage" />
            </StatGrid>
          </Surface>
          <Surface title="Energy over time" subtitle="Tier and time-of-use context is available in each interval tooltip.">
            {history.data.points.length ? (
              <EnergyChart points={history.data.points} mode={filters.metric} currency={home.currency} title={`${history.data.title} ${filters.metric} history`} />
            ) : (
              <EmptyState title="No readings in this range" message="Try a wider range or check the sensor’s connection." />
            )}
          </Surface>
          {history.data.ratePlans.length > 0 && (
            <p className="history-provenance">Calculated using historically effective plan{history.data.ratePlans.length > 1 ? 's' : ''}: {history.data.ratePlans.join(', ')}.</p>
          )}
        </>
      ) : null}
    </Page>
  )
}
