import { useMutation, useQuery } from '@tanstack/react-query'
import { AlertTriangle, CalendarRange, Download, FlaskConical, Gauge, Layers3 } from 'lucide-react'
import { useState } from 'react'
import { adaptHistory, adaptTestModeHistory } from '../../api/adapters'
import { download, errorMessage, json, request, saveBlob } from '../../api/client'
import { EnergyChart } from '../../components/charts/EnergyChart'
import { Metric, Surface } from '../../components/data-display/Surface'
import { EmptyState, ErrorState, InlineNotice, LoadingState } from '../../components/feedback/States'
import { Page, PageHeader, SegmentedControl, StatGrid } from '../../components/layout/Layout'
import { HISTORY_REFETCH_INTERVAL_MS, historyPayload } from '../../features/history/historyQuery'
import { useLiveHome } from '../../state/LiveHomeContext'
import { useSingleHome } from '../../state/SingleHomeContext'
import { useTestMode } from '../../state/TestModeContext'
import type { HistoryFilters, HistoryMetric, HistoryRange, HistoryScope } from '../../types/models'
import { energy, money, percentage, power, rate } from '../../utils/format'
import { hasPermission } from '../../access/permissions'
import { useAuth } from '../../state/AuthContext'

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
  const { session } = useAuth()
  const canViewCosts = hasPermission(session, 'costs.view')
  const canExport = hasPermission(session, 'history.export')
  const { resolution } = useSingleHome()
  const { sensors, cycle } = useLiveHome()
  const testMode = useTestMode()
  const home = resolution?.state === 'ready' ? resolution.home : undefined
  const [filters, setFilters] = useState<HistoryFilters>({
    range: '7d',
    metric: canViewCosts ? 'energy_cost' : 'energy',
    scope: 'home',
  })
  const validScope = filters.scope === 'home' || Boolean(filters.sensorId)
  const history = useQuery({
    queryKey: ['history', 'page', home?.id, filters, cycle?.startsAt, cycle?.endsAt],
    queryFn: () => {
      if (!home) throw new Error('The default home is unavailable.')
      const currentPayload = historyPayload(filters, home, cycle?.startsAt, cycle?.endsAt)
      return request('/api/v1/history/query', json('POST', currentPayload), adaptHistory)
    },
    enabled: Boolean(home && validScope),
    placeholderData: (previous) => previous,
    refetchInterval: HISTORY_REFETCH_INTERVAL_MS,
  })
  const testHistory = useQuery({
    queryKey: ['sensor-test-mode-history'],
    queryFn: () => request('/api/v1/test-mode/history?limit=120', {}, adaptTestModeHistory),
    enabled: Boolean(testMode.state?.enabled),
    refetchInterval: testMode.state?.enabled ? 5_000 : false,
  })
  const exportHistory = useMutation({
    mutationFn: async () => {
      if (!home) throw new Error('The default home is unavailable.')
      const currentPayload = historyPayload(filters, home, cycle?.startsAt, cycle?.endsAt)
      const blob = await download('/api/v1/history/export', json('POST', currentPayload))
      saveBlob(blob, `power-monitor-${filters.range}-history.csv`)
    },
  })

  if (!home) return <ErrorState error={new Error('The default home is unavailable.')} />

  return (
    <Page className="history-page">
      <PageHeader
        title="History"
        description="See how your home used energy and what it cost over time."
        action={canExport && <button type="button" className="button secondary" disabled={!validScope || exportHistory.isPending} onClick={() => { exportHistory.mutate(); }}>
          <Download size={17} /> {exportHistory.isPending ? 'Preparing…' : 'Export'}
        </button>}
      />
      {testMode.state?.enabled && (
        <Surface className="test-mode-surface active" title="Test Mode history" subtitle="Ephemeral synthetic samples · excluded from the History export and all real coverage calculations.">
          <div className="test-mode-inline-heading">
            <FlaskConical />
            <span><strong>{testMode.state.sensorCount} simulated sensors</strong><small>{testMode.state.loadProfile?.replaceAll('_', ' ')} profile · {testMode.state.expiresAt ? 'automatically discarded at expiry' : 'discarded when Test Mode is disabled'}</small></span>
            <span className="pill warning">Test Mode</span>
          </div>
          <StatGrid className="test-mode-summary">
            <Metric label="Current simulated load" value={power(testMode.state.currentPowerW)} identity="test_mode.history.load" />
            <Metric label="Synthetic energy" value={energy(testMode.state.totalEnergyKwh)} identity="test_mode.history.energy" />
            <Metric label="Samples in memory" value={testHistory.data?.length ?? 0} identity="test_mode.history.samples" />
          </StatGrid>
          {testHistory.data?.length ? (
            <div className="test-history-list">
              {testHistory.data.slice(-12).reverse().map((point) => <div key={`${point.sensorId}-${point.recordedAt}`}><span>{point.sensorName}</span><strong>{point.online ? power(point.powerW) : 'Offline'}</strong><small>{new Date(point.recordedAt).toLocaleTimeString()}</small></div>)}
            </div>
          ) : <LoadingState label="Waiting for isolated test samples…" />}
        </Surface>
      )}
      {exportHistory.error && <InlineNotice tone="danger">{errorMessage(exportHistory.error)}</InlineNotice>}

      <Surface className="history-controls-surface">
        <SegmentedControl label="History range" value={filters.range} items={ranges} onChange={(range) => { setFilters((current) => ({ ...current, range })); }} />
        <div className="history-filter-row">
          <label>
            <span><Gauge size={15} /> Metric</span>
            <select value={filters.metric} onChange={(event) => { setFilters((current) => ({ ...current, metric: event.target.value as HistoryMetric })); }}>
              {metrics.filter((item) => canViewCosts || !['cost', 'energy_cost'].includes(item.value)).map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
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
              Coverage is {percentage(history.data.coveragePercent)}. Missing readings remain gaps and are not replaced with zero.
            </InlineNotice>
          )}
          {history.data.warnings.map((warning) => <InlineNotice key={warning} tone="warning">{warning}</InlineNotice>)}
          <Surface title={history.data.title} subtitle={`${filters.scope === 'home' ? 'Whole Home' : 'Individual sensor'} · ${history.data.contributingSensors} contributing sensor${history.data.contributingSensors === 1 ? '' : 's'}`}>
            <StatGrid className="history-summary">
              <Metric label="Energy" value={energy(history.data.energyKwh)} identity="history.energy" />
              {canViewCosts && <Metric label="Estimated cost" value={money(history.data.cost, home.currency)} identity="history.cost" detail="Interval energy charges" />}
              {canViewCosts && <Metric label="Blended rate" value={rate(history.data.blendedRate, home.currency)} identity="history.blended_rate" />}
              <Metric label="Peak power" value={power(history.data.peakPowerW)} identity="history.peak_power" />
              <Metric label="Coverage" value={percentage(history.data.coveragePercent)} identity="data.coverage" />
            </StatGrid>
          </Surface>
          <Surface title="Energy over time" subtitle="Tier and time-of-use context is available in each interval tooltip.">
            {history.data.points.length ? (
              <EnergyChart points={history.data.points} mode={filters.metric} currency={home.currency} title={`${history.data.title} ${filters.metric} history`} timezone={history.data.timezone} bucket={history.data.bucket} rangeStart={history.data.rangeStart} rangeEnd={history.data.rangeEnd} variant="history" />
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
