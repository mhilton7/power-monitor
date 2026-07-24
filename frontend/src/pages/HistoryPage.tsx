import { useMutation, useQuery } from '@tanstack/react-query'
import {
  CategoryScale,
  Chart as ChartJS,
  Filler,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  TimeScale,
  Tooltip,
  type ChartData,
  type ChartOptions,
} from 'chart.js'
import { Chart } from 'react-chartjs-2'
import {
  AlertTriangle,
  CalendarRange,
  Check,
  ChevronLeft,
  ChevronRight,
  Download,
  Info,
  Layers3,
  SlidersHorizontal,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ApiError, api, apiDownload, jsonBody } from '../api'
import { StatusIndicatorZone } from '../components/StatusIndicators'
import type {
  AggregateSet,
  Circuit,
  Device,
  HistoryBucket,
  HistoryDisplayMode,
  HistoryMetric,
  HistoryQueryRequest,
  HistoryQueryResponse,
  HistoryScopeRequest,
  HistoryScopeType,
  Site,
} from '../types'
import {
  EmptyState,
  ErrorState,
  formatMoney,
  formatNumber,
  LoadingState,
  PageTitle,
  Panel,
  StatusPill,
} from '../components/UI'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineController, LineElement, TimeScale, Tooltip, Filler, Legend)

const palette = ['#087a65', '#2563eb', '#c2410c', '#7c3aed', '#be123c', '#0369a1', '#4d7c0f']

const metricOptions: Array<{ value: HistoryMetric; label: string }> = [
  { value: 'power_w', label: 'Active power (W)' },
  { value: 'energy_kwh', label: 'Energy (kWh)' },
  { value: 'voltage_v', label: 'Voltage (V)' },
  { value: 'current_a', label: 'Current (A)' },
  { value: 'power_factor', label: 'Power factor' },
  { value: 'frequency_hz', label: 'Frequency (Hz)' },
  { value: 'energy_cost', label: 'Energy cost' },
  { value: 'usage_cost', label: 'Usage + cost' },
]

interface SelectionRange {
  start: string
  end: string
}

interface PersistedHistoryPreferences {
  siteId?: string
  scopeType?: HistoryScopeType
  selectedDeviceIds?: string[]
  circuitId?: string
  aggregateId?: string
  displayMode?: HistoryDisplayMode
  metric?: HistoryMetric
  rangeHours?: number
  bucket?: HistoryQueryRequest['bucket']
  strictCoverage?: boolean
}

function readPreferences(): PersistedHistoryPreferences {
  try {
    return JSON.parse(localStorage.getItem('pm-history-preferences') ?? '{}') as PersistedHistoryPreferences
  } catch {
    return {}
  }
}

function isAncestor(childId: string | undefined, ancestorId: string | undefined, circuits: Circuit[]): boolean {
  if (!childId || !ancestorId) return false
  if (childId === ancestorId) return true
  const byId = new Map(circuits.map((circuit) => [circuit.id, circuit]))
  const visited = new Set<string>()
  let current = byId.get(childId)
  while (current?.parent_id && !visited.has(current.parent_id)) {
    if (current.parent_id === ancestorId) return true
    visited.add(current.parent_id)
    current = byId.get(current.parent_id)
  }
  return false
}

function eligibleDeviceIds(devices: Device[], circuits: Circuit[]): string[] {
  const depth = (device: Device) => {
    let result = 0
    let current = device.circuit_id
    while (current) {
      const parent = circuits.find((item) => item.id === current)?.parent_id
      if (!parent) break
      result += 1
      current = parent
    }
    return result
  }
  const selected: Device[] = []
  for (const device of [...devices].sort((left, right) => depth(left) - depth(right) || left.name.localeCompare(right.name))) {
    const overlaps = selected.some((item) =>
      isAncestor(device.circuit_id, item.circuit_id, circuits) ||
      isAncestor(item.circuit_id, device.circuit_id, circuits),
    )
    if (!overlaps) selected.push(device)
  }
  return selected.map((device) => device.id)
}

function formatInterval(point: HistoryBucket): string {
  const start = new Date(point.local_start)
  const end = new Date(point.local_end)
  const formatter = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
  return `${formatter.format(start)}–${formatter.format(end)} (${point.utc_offset})`
}

function unavailableMoney(value: string | undefined): string {
  return value === undefined ? 'Unavailable' : formatMoney(value)
}

function unavailableNumber(value: string | undefined, digits = 3): string {
  return value === undefined ? 'Unavailable' : formatNumber(value, digits)
}

function metricValue(point: HistoryBucket, metric: HistoryMetric): number | null {
  const value = {
    power_w: point.average_power_w,
    energy_kwh: point.energy_kwh,
    voltage_v: point.voltage_avg_v,
    current_a: point.current_a,
    power_factor: point.power_factor,
    frequency_hz: point.frequency_hz,
    energy_cost: point.energy_cost,
    usage_cost: point.energy_kwh,
  }[metric]
  return value === undefined ? null : Number(value)
}

function metricUnit(metric: HistoryMetric): string {
  return {
    power_w: 'W',
    energy_kwh: 'kWh',
    voltage_v: 'V average',
    current_a: 'A',
    power_factor: '',
    frequency_hz: 'Hz',
    energy_cost: 'USD',
    usage_cost: 'kWh',
  }[metric]
}

export function HistoryPage() {
  const [search] = useSearchParams()
  const initial = useMemo(readPreferences, [])
  const [siteId, setSiteId] = useState(initial.siteId ?? '')
  const [scopeType, setScopeType] = useState<HistoryScopeType>(initial.scopeType ?? 'device')
  const [selectedDeviceIds, setSelectedDeviceIds] = useState<string[]>(
    search.get('device_id') ? [search.get('device_id') ?? ''] : initial.selectedDeviceIds ?? [],
  )
  const [circuitId, setCircuitId] = useState(initial.circuitId ?? '')
  const [aggregateId, setAggregateId] = useState(initial.aggregateId ?? '')
  const [displayMode, setDisplayMode] = useState<HistoryDisplayMode>(initial.displayMode ?? 'combined')
  const [metric, setMetric] = useState<HistoryMetric>(initial.metric ?? 'power_w')
  const [rangeHours, setRangeHours] = useState(initial.rangeHours ?? 24)
  const [bucket, setBucket] = useState<HistoryQueryRequest['bucket']>(initial.bucket ?? 'auto')
  const [strictCoverage, setStrictCoverage] = useState(initial.strictCoverage ?? false)
  const [page, setPage] = useState(1)
  const [selection, setSelection] = useState<SelectionRange>()
  const [downloadMessage, setDownloadMessage] = useState('')
  const [anchor, setAnchor] = useState(() => new Date())

  const sites = useQuery({ queryKey: ['sites'], queryFn: () => api<Site[]>('/api/v1/sites') })
  const devices = useQuery({ queryKey: ['devices'], queryFn: () => api<Device[]>('/api/v1/devices') })
  const circuits = useQuery({ queryKey: ['circuits'], queryFn: () => api<Circuit[]>('/api/v1/circuits') })
  const aggregates = useQuery({ queryKey: ['aggregates'], queryFn: () => api<AggregateSet[]>('/api/v1/aggregate-sets') })

  const currentSite = sites.data?.find((site) => site.id === siteId)
  const siteDevices = useMemo(
    () => (devices.data ?? []).filter((device) => device.site_id === siteId && device.lifecycle_status !== 'decommissioned'),
    [devices.data, siteId],
  )
  const siteCircuits = useMemo(
    () => (circuits.data ?? []).filter((circuit) => circuit.site_id === siteId),
    [circuits.data, siteId],
  )
  const siteAggregates = useMemo(
    () => (aggregates.data ?? []).filter((aggregate) => aggregate.site_id === siteId),
    [aggregates.data, siteId],
  )

  useEffect(() => {
    if (!siteId && sites.data?.[0]) setSiteId(sites.data[0].id)
  }, [siteId, sites.data])

  useEffect(() => {
    const valid = selectedDeviceIds.filter((id) => siteDevices.some((device) => device.id === id))
    const requested = search.get('device_id')
    const requestedDevice = siteDevices.find((device) => device.id === requested)
    if (requestedDevice && (siteId !== requestedDevice.site_id || valid[0] !== requestedDevice.id)) {
      setSiteId(requestedDevice.site_id)
      setSelectedDeviceIds([requestedDevice.id])
      setScopeType('device')
      return
    }
    if (!valid.length && siteDevices[0]) setSelectedDeviceIds([siteDevices[0].id])
    else if (valid.length !== selectedDeviceIds.length) setSelectedDeviceIds(valid)
  }, [search, selectedDeviceIds, siteDevices, siteId])

  useEffect(() => {
    if (!circuitId && siteCircuits[0]) setCircuitId(siteCircuits[0].id)
    if (!aggregateId && siteAggregates[0]) setAggregateId(siteAggregates[0].id)
  }, [aggregateId, circuitId, siteAggregates, siteCircuits])

  useEffect(() => {
    localStorage.setItem('pm-history-preferences', JSON.stringify({
      siteId,
      scopeType,
      selectedDeviceIds,
      circuitId,
      aggregateId,
      displayMode,
      metric,
      rangeHours,
      bucket,
      strictCoverage,
    } satisfies PersistedHistoryPreferences))
  }, [aggregateId, bucket, circuitId, displayMode, metric, rangeHours, scopeType, selectedDeviceIds, siteId, strictCoverage])

  const scope = useMemo<HistoryScopeRequest | undefined>(() => {
    if (scopeType === 'device' && selectedDeviceIds[0]) return { type: 'device', device_id: selectedDeviceIds[0] }
    if (scopeType === 'devices' && selectedDeviceIds.length >= 2) return { type: 'devices', device_ids: selectedDeviceIds }
    if (scopeType === 'circuit' && circuitId) return { type: 'circuit', circuit_id: circuitId }
    if (scopeType === 'site' && siteId) return { type: 'site', site_id: siteId }
    if (scopeType === 'aggregate_set' && aggregateId) return { type: 'aggregate_set', aggregate_set_id: aggregateId }
    return undefined
  }, [aggregateId, circuitId, scopeType, selectedDeviceIds, siteId])

  const start = useMemo(() => new Date(anchor.getTime() - rangeHours * 3_600_000), [anchor, rangeHours])
  const metrics = useMemo<HistoryMetric[]>(() =>
    metric === 'usage_cost' ? ['energy_kwh', 'energy_cost', 'usage_cost'] :
      metric === 'energy_cost' ? ['energy_kwh', 'energy_cost'] : [metric], [metric])
  const payload = useMemo<HistoryQueryRequest | undefined>(() => scope ? ({
    scope,
    display_mode: scopeType === 'device' ? 'combined' : displayMode,
    metrics,
    start_utc: start.toISOString(),
    end_utc: anchor.toISOString(),
    bucket,
    timezone: currentSite?.timezone,
    strict_coverage: strictCoverage,
    selection_start_utc: selection?.start,
    selection_end_utc: selection?.end,
    page,
    page_size: 250,
  }) : undefined, [anchor, bucket, currentSite?.timezone, displayMode, metrics, page, scope, scopeType, selection, start, strictCoverage])

  const history = useQuery({
    queryKey: ['history-query', payload],
    enabled: Boolean(payload),
    queryFn: () => api<HistoryQueryResponse>('/api/v1/history/query', jsonBody(payload)),
  })

  const exportHistory = useMutation({
    mutationFn: async () => {
      if (!payload) throw new Error('Select a valid history scope first.')
      return apiDownload('/api/v1/history/export', jsonBody(payload))
    },
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'power-monitor-history.csv'
      link.click()
      URL.revokeObjectURL(url)
      setDownloadMessage('History export downloaded.')
    },
  })

  const queryData = history.data
  const allSeries = useMemo(() => {
    if (!queryData) return []
    const result: Array<{ id: string; name: string; points: HistoryBucket[]; combined: boolean }> = []
    if (queryData.combined.length) result.push({ id: 'combined', name: queryData.scope.display_name, points: queryData.combined, combined: true })
    for (const series of queryData.individual) result.push({ id: series.device_id, name: series.name, points: series.points, combined: false })
    return result
  }, [queryData])
  const basePoints = useMemo(
    () => queryData?.combined.length ? queryData.combined : queryData?.individual[0]?.points ?? [],
    [queryData],
  )

  const chartData = useMemo<ChartData<'line'>>(() => {
    const labels = basePoints.map((point) => new Date(point.interval_start_utc).toISOString())
    const datasets = allSeries.flatMap((series, index) => {
      const color = series.combined ? '#087a65' : palette[index % palette.length]
      const common = {
        borderColor: color,
        backgroundColor: series.combined ? 'rgba(8,122,101,.14)' : `${color}18`,
        borderWidth: series.combined ? 3 : 1.75,
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.2,
        fill: series.combined,
        historySeriesId: series.id,
      }
      if (metric === 'usage_cost') {
        return [
          { ...common, label: `${series.name} energy`, data: series.points.map((point) => metricValue(point, 'energy_kwh')), yAxisID: 'y' },
          { ...common, label: `${series.name} cost`, data: series.points.map((point) => metricValue(point, 'energy_cost')), yAxisID: 'yCost', borderDash: [5, 4], fill: false },
        ]
      }
      return [{
        ...common,
        label: `${series.name}${metric === 'voltage_v' && allSeries.length > 1 ? ' average voltage' : ''}`,
        data: series.points.map((point) => metricValue(point, metric)),
        yAxisID: metric === 'energy_cost' ? 'yCost' : 'y',
      }]
    })
    return { labels, datasets }
  }, [allSeries, basePoints, metric])

  const chartOptions = useMemo<ChartOptions<'line'>>(() => ({
    responsive: true,
    maintainAspectRatio: false,
    interaction: { intersect: false, mode: 'index' },
    animation: { duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 250 },
    scales: {
      x: {
        grid: { display: false },
        ticks: {
          maxTicksLimit: 8,
          color: '#617b76',
          callback: (_value, index) => basePoints[index] ? new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(basePoints[index].local_start)) : '',
        },
      },
      y: {
        display: metric !== 'energy_cost',
        position: 'left',
        title: { display: true, text: metric === 'usage_cost' ? 'Energy (kWh)' : metricOptions.find((item) => item.value === metric)?.label ?? metric },
        grid: { color: 'rgba(98,123,118,.13)' },
        ticks: { color: '#617b76' },
      },
      yCost: {
        display: metric === 'energy_cost' || metric === 'usage_cost',
        position: 'right',
        title: { display: true, text: 'Estimated energy cost (USD)' },
        grid: { drawOnChartArea: metric === 'energy_cost' },
        ticks: { color: '#087a65', callback: (value) => formatMoney(Number(value)) },
      },
    },
    plugins: {
      legend: { display: allSeries.length > 1 || metric === 'usage_cost', labels: { usePointStyle: true } },
      tooltip: {
        callbacks: {
          title: (items) => {
            const index = items[0]?.dataIndex ?? 0
            return basePoints[index] ? formatInterval(basePoints[index]) : ''
          },
          label: (context) => {
            const suffix = context.dataset.yAxisID === 'yCost' ? ' USD' : ` ${metricUnit(metric)}`
            return `${context.dataset.label ?? 'Series'}: ${formatNumber(context.parsed.y, 5)}${suffix}`
          },
          afterBody: (items) => {
            const item = items[0]
            if (!item) return []
            const seriesId = (item.dataset as typeof item.dataset & { historySeriesId?: string }).historySeriesId
            const series = allSeries.find((value) => value.id === seriesId)
            const point = series?.points[item.dataIndex]
            if (!point) return []
            const lines = [
              `Scope: ${point.series_name}`,
              `Sensors: ${point.contributing_sensor_count}/${point.included_sensor_count}`,
              `Energy: ${unavailableNumber(point.energy_kwh, 6)} kWh`,
              `Average power: ${unavailableNumber(point.average_power_w, 2)} W`,
              `Peak power: ${unavailableNumber(point.peak_power_w, 2)} W`,
              `TOU period: ${point.tou_period ?? 'Unavailable'}`,
              `Rate: ${point.rate_per_kwh ? `${formatMoney(point.rate_per_kwh)}/kWh` : 'Unavailable'}`,
              `Estimated energy cost: ${unavailableMoney(point.energy_cost)}`,
              `Rate plan: ${point.rate_plan_name ?? 'Unavailable'}`,
              `Rate version: ${point.rate_version_id ?? 'Unavailable'}${point.rate_effective_from ? ` (effective ${point.rate_effective_from})` : ''}`,
              `Coverage: ${formatNumber(point.coverage_percent, 2)}%`,
            ]
            if (point.quality_flags.length) lines.push(`Quality: ${point.quality_flags.join(', ')}`)
            if (point.rate_contributions.length > 1) {
              lines.push(...point.rate_contributions.map((part) =>
                `${part.rate_plan_name} · ${part.tier_name ? `${part.tier_name} / ` : ''}${part.tou_period}: ${part.energy_kwh} kWh at ${formatMoney(part.rate_per_kwh)}/kWh = ${formatMoney(part.energy_cost)}${part.cumulative_start_kwh ? ` (cycle ${part.cumulative_start_kwh}-${part.cumulative_end_kwh} kWh)` : ''}`,
              ))
            }
            return lines
          },
        },
      },
    },
  }), [allSeries, basePoints, metric])

  const selectedDevices = siteDevices.filter((device) => selectedDeviceIds.includes(device.id))
  const overlapNames = useMemo(() => {
    const names = new Set<string>()
    for (let index = 0; index < selectedDevices.length; index += 1) {
      for (let right = index + 1; right < selectedDevices.length; right += 1) {
        const leftDevice = selectedDevices[index]
        const rightDevice = selectedDevices[right]
        if (!leftDevice || !rightDevice) continue
        if (isAncestor(leftDevice.circuit_id, rightDevice.circuit_id, siteCircuits) || isAncestor(rightDevice.circuit_id, leftDevice.circuit_id, siteCircuits)) {
          names.add(leftDevice.name)
          names.add(rightDevice.name)
        }
      }
    }
    return [...names]
  }, [selectedDevices, siteCircuits])

  const toggleDevice = (deviceId: string) => {
    setPage(1)
    setSelection(undefined)
    setSelectedDeviceIds((current) => current.includes(deviceId) ? current.filter((id) => id !== deviceId) : [...current, deviceId])
  }

  const selectTableRange = (point: HistoryBucket) => {
    setPage(1)
    setSelection((current) => {
      if (!current) return { start: point.interval_start_utc, end: point.interval_end_utc }
      const startValue = Math.min(new Date(current.start).getTime(), new Date(point.interval_start_utc).getTime())
      const endValue = Math.max(new Date(current.end).getTime(), new Date(point.interval_end_utc).getTime())
      return { start: new Date(startValue).toISOString(), end: new Date(endValue).toISOString() }
    })
  }

  const selectionIncludes = (point: HistoryBucket) => Boolean(selection &&
    new Date(point.interval_start_utc).getTime() >= new Date(selection.start).getTime() &&
    new Date(point.interval_end_utc).getTime() <= new Date(selection.end).getTime())

  const rangeSummary = queryData?.selected_summary ?? queryData?.summary
  const invalidMultiSelection = scopeType === 'devices' && selectedDeviceIds.length < 2
  const loading = sites.isLoading || devices.isLoading || circuits.isLoading || aggregates.isLoading
  const sourceError = sites.error ?? devices.error ?? circuits.error ?? aggregates.error
  const costUnavailable = queryData?.warnings.some((warning) => warning.code === 'rate_unavailable')

  return (
    <>
      <PageTitle
        eyebrow="Measurement explorer"
        title="History & comparison"
        description="Compare authorized sensors, physically valid totals, and historically effective time-of-use energy costs."
        actions={<button className="button secondary" onClick={() => { exportHistory.mutate() }} disabled={!payload || exportHistory.isPending}>
          <Download size={17} /> {exportHistory.isPending ? 'Preparing…' : 'Export CSV'}
        </button>}
      />
      <StatusIndicatorZone zone="history_context" />
      {downloadMessage && <p className="success-message" role="status"><Check size={16} /> {downloadMessage}</p>}
      {exportHistory.error && <p className="field-error" role="alert">{exportHistory.error instanceof ApiError ? exportHistory.error.problem.detail : 'The export could not be created.'}</p>}

      <Panel className="history-panel history-controls-panel" title="History scope" eyebrow="Server-authoritative query">
        {loading ? <LoadingState label="Loading authorized history options…" /> : sourceError ? <ErrorState error={sourceError} /> : <>
          <div className="history-controls history-scope-controls">
            <label><span>Site</span><select value={siteId} onChange={(event) => { setSiteId(event.target.value); setSelectedDeviceIds([]); setCircuitId(''); setAggregateId(''); setSelection(undefined); setPage(1) }}>{sites.data?.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}</select></label>
            <label><span><Layers3 size={15} /> Scope</span><select value={scopeType} onChange={(event) => { setScopeType(event.target.value as HistoryScopeType); setSelection(undefined); setPage(1) }}>
              <option value="device">Single sensor</option>
              <option value="devices">Multiple sensors</option>
              <option value="circuit">Circuit</option>
              <option value="site">Site total</option>
              {siteAggregates.length > 0 && <option value="aggregate_set">Saved aggregate set</option>}
            </select></label>
            {scopeType === 'device' && <label><span><SlidersHorizontal size={15} /> Sensor</span><select value={selectedDeviceIds[0] ?? ''} onChange={(event) => { setSelectedDeviceIds([event.target.value]); setPage(1); setSelection(undefined) }}>{siteDevices.map((device) => <option key={device.id} value={device.id}>{device.name} · {device.id.slice(-8)}</option>)}</select></label>}
            {scopeType === 'circuit' && <label><span>Circuit</span><select value={circuitId} onChange={(event) => { setCircuitId(event.target.value); setPage(1); setSelection(undefined) }}>{siteCircuits.map((circuit) => <option key={circuit.id} value={circuit.id}>{circuit.name} · {circuit.measurement_role}</option>)}</select></label>}
            {scopeType === 'aggregate_set' && <label><span>Saved aggregate</span><select value={aggregateId} onChange={(event) => { setAggregateId(event.target.value); setPage(1); setSelection(undefined) }}>{siteAggregates.map((aggregate) => <option key={aggregate.id} value={aggregate.id}>{aggregate.name}</option>)}</select></label>}
            {scopeType !== 'device' && <label><span>Display mode</span><select value={displayMode} onChange={(event) => { setDisplayMode(event.target.value as HistoryDisplayMode); setPage(1) }}><option value="combined">Combined total</option><option value="individual">Individual sensors</option><option value="combined_plus_individual">Combined + individual</option></select></label>}
            <label><span>Metric</span><select value={metric} onChange={(event) => { setMetric(event.target.value as HistoryMetric); setPage(1) }}>{metricOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
            <label><span><CalendarRange size={15} /> Range</span><select value={rangeHours} onChange={(event) => { setRangeHours(Number(event.target.value)); setAnchor(new Date()); setPage(1); setSelection(undefined) }}><option value={6}>Last 6 hours</option><option value={24}>Last 24 hours</option><option value={168}>Last 7 days</option><option value={720}>Last 30 days</option></select></label>
            <label><span>Bucket</span><select value={bucket} onChange={(event) => { setBucket(event.target.value as HistoryQueryRequest['bucket']); setPage(1); setSelection(undefined) }}><option value="auto">Automatic</option><option value="raw">Raw (up to 2 days)</option><option value="5m">5 minutes</option><option value="15m">15 minutes</option><option value="1h">Hourly</option><option value="1d">Daily</option></select></label>
          </div>
          {scopeType === 'devices' && <fieldset className="history-multi-select">
            <legend>Choose two or more sensors</legend>
            <div className="history-multi-actions"><button type="button" className="button secondary compact" onClick={() => { setSelectedDeviceIds(eligibleDeviceIds(siteDevices, siteCircuits)); setPage(1); setSelection(undefined) }}>Select all eligible sensors</button><span>{selectedDeviceIds.length} selected</span></div>
            <div className="history-device-options">{siteDevices.map((device) => <label key={device.id} className={selectedDeviceIds.includes(device.id) ? 'selected' : ''}><input type="checkbox" checked={selectedDeviceIds.includes(device.id)} onChange={() => { toggleDevice(device.id) }} /><span><strong>{device.name}</strong><small>{device.id.slice(-8)} · {device.site_name ?? currentSite?.name} · {device.circuit_name ?? 'No circuit'} · {device.status.replaceAll('_', ' ')}</small></span><StatusPill status={device.status} /></label>)}</div>
          </fieldset>}
          <label className="history-strict-toggle"><input type="checkbox" checked={strictCoverage} onChange={(event) => { setStrictCoverage(event.target.checked); setPage(1) }} /><span><strong>Require complete coverage</strong><small>Withhold combined values when any selected sensor is missing instead of presenting a transparent partial total.</small></span></label>
          {invalidMultiSelection && <p className="history-inline-warning"><Info size={16} /> Select at least two sensors for a multi-sensor query.</p>}
          {overlapNames.length > 0 && <p className="history-inline-warning"><AlertTriangle size={16} /> Topology overlap: {overlapNames.join(', ')} cannot be added into a combined total. Use Individual sensors for comparison.</p>}
        </>}
      </Panel>

      {history.isLoading && <Panel><LoadingState label="Aligning intervals and calculating historical rates…" /></Panel>}
      {history.error && <Panel><ErrorState error={history.error} retry={() => void history.refetch()} /></Panel>}
      {!history.isLoading && !history.error && invalidMultiSelection && <Panel><EmptyState title="Choose at least two sensors" message="The combined query starts after two authorized sensors are selected." /></Panel>}
      {queryData && <>
        <section className="history-scope-summary" aria-label="Selected history scope">
          <div><strong>{queryData.display_mode === 'individual' ? 'Individual sensors' : 'Combined total'} · {queryData.scope.included_device_ids.length} {queryData.scope.included_device_ids.length === 1 ? 'sensor' : 'sensors'} · {queryData.scope.included_device_names.join(' + ')}</strong><small>{queryData.scope.site_name} · {rangeHours < 48 ? `${rangeHours} hours` : `${Math.round(rangeHours / 24)} days`} · {queryData.bucket} buckets · {queryData.scope.mixed_rates ? 'Mixed rates' : queryData.rate_versions_used[0]?.rate_plan_name ?? 'No active rate'}</small></div>
          {basePoints.length > 0 && <span data-metric-identity="data.coverage"><StatusPill status={Number(queryData.summary.coverage_percent) >= 99 ? 'healthy' : 'pending'} label={`${formatNumber(queryData.summary.coverage_percent, 1)}% coverage`} /></span>}
        </section>
        {basePoints.length > 0 && queryData.warnings.filter((warning) => warning.code !== 'rate_unavailable').map((warning) => <aside className="history-warning" key={`${warning.code}-${warning.device_ids?.join('-') ?? ''}`} role="status"><AlertTriangle size={18} /><p><strong>{warning.code.replaceAll('_', ' ')}</strong><span>{warning.message}</span></p></aside>)}

        {basePoints.length > 0 && <div className="history-summary-grid">
          <article><span>Total energy</span><strong>{unavailableNumber(rangeSummary?.energy_kwh, 4)} kWh</strong></article>
          <article><span>Estimated energy cost</span><strong>{unavailableMoney(rangeSummary?.energy_cost)}</strong><small>Selected sensors; account-level fixed charges excluded</small></article>
          <article><span>Blended energy rate</span><strong>{rangeSummary?.blended_rate_per_kwh ? `${formatMoney(rangeSummary.blended_rate_per_kwh)}/kWh` : 'Unavailable'}</strong></article>
          <article><span>Highest-cost bucket</span><strong>{unavailableMoney(rangeSummary?.highest_cost_bucket_value)}</strong><small>{rangeSummary?.highest_cost_bucket_start ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(rangeSummary.highest_cost_bucket_start)) : 'No cost data'}</small></article>
          <article data-metric-identity="power.recent_peak"><span>Recent peak</span><strong>{unavailableNumber(rangeSummary?.peak_power_w, 2)} W</strong></article>
        </div>}

        <Panel className="history-panel" title="Historical measurements" eyebrow="Aligned intervals and rate provenance" actions={selection ? <button className="button secondary compact" onClick={() => { setSelection(undefined) }}><X size={15} /> Clear selected range</button> : undefined}>
          {basePoints.length ? <>
            <p className="sr-only" role="img" aria-label={`History chart for ${queryData.scope.display_name}. ${unavailableNumber(queryData.summary.energy_kwh, 4)} kilowatt-hours and ${unavailableMoney(queryData.summary.energy_cost)} estimated energy cost with ${formatNumber(queryData.summary.coverage_percent, 2)} percent coverage.`} />
            <div className="history-chart"><Chart type="line" data={chartData} options={chartOptions} /></div>
          </> : <EmptyState title={queryData.scope.included_device_ids.length ? 'No readings in this range' : 'No sensors in this scope'} message={queryData.scope.included_device_ids.length ? 'History appears after durable readings synchronize from sensor storage. Try a wider range or review device connectivity.' : 'Choose another scope or enroll a sensor before requesting historical measurements.'} action={<Link className="button secondary" to={queryData.scope.included_device_ids.length ? '/devices' : '/enrollment'}>{queryData.scope.included_device_ids.length ? 'Review devices' : 'Open enrollment'}</Link>} />}
        </Panel>

        {selection && queryData.selected_summary && <Panel title="Selected range summary" eyebrow="Server-calculated interval segments">
          <div className="selected-range-heading"><p><strong>{new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(selection.start))}–{new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(selection.end))}</strong><small>Select another interval in the table to extend this contiguous range.</small></p><StatusPill status="healthy" label={`${formatNumber(queryData.selected_summary.coverage_percent, 1)}% coverage`} /></div>
          <div className="history-selected-grid"><span><small>Energy</small><strong>{unavailableNumber(queryData.selected_summary.energy_kwh, 5)} kWh</strong></span><span><small>Estimated energy cost</small><strong>{unavailableMoney(queryData.selected_summary.energy_cost)}</strong></span><span><small>Weighted rate</small><strong>{queryData.selected_summary.blended_rate_per_kwh ? `${formatMoney(queryData.selected_summary.blended_rate_per_kwh)}/kWh` : 'Unavailable'}</strong></span><span><small>Average / peak power</small><strong>{unavailableNumber(queryData.selected_summary.average_power_w, 1)} / {unavailableNumber(queryData.selected_summary.peak_power_w, 1)} W</strong></span></div>
          <div className="tou-breakdown">{Object.entries(queryData.selected_summary.tou_breakdown).map(([period, values]) => <span key={period}><strong>{period}</strong><small>{formatNumber(values.energy_kwh, 4)} kWh · {formatMoney(values.energy_cost)}</small></span>)}</div>
        </Panel>}

        {basePoints.length > 0 && <Panel title="Interval details" eyebrow="Accessible chart alternative" actions={<span className="count-badge">{queryData.total_buckets} buckets</span>}>
          <p className="history-table-help">Select any interval checkbox to start or extend a server-calculated range summary. Missing data remains unavailable and is never replaced with zero.</p>
          <div className="responsive-table history-table"><table>
            <thead><tr><th scope="col">Select</th><th scope="col">Series</th><th scope="col">Interval</th><th scope="col">Energy</th><th scope="col">Average / peak power</th><th scope="col">TOU period</th><th scope="col">Rate</th><th scope="col">Estimated energy cost</th><th scope="col">Rate plan / version</th><th scope="col">Coverage / quality</th></tr></thead>
            <tbody>{allSeries.flatMap((series) => series.points.map((point) => <tr key={`${series.id}-${point.interval_start_utc}`}>
              <td>{series.id === (queryData.combined.length ? 'combined' : queryData.individual[0]?.device_id) ? <input type="checkbox" aria-label={`Include ${formatInterval(point)} in selected range`} checked={selectionIncludes(point)} onChange={() => { selectTableRange(point) }} /> : <span aria-hidden="true">—</span>}</td>
              <th scope="row"><strong>{point.series_name}</strong><small>{point.contributing_sensor_count}/{point.included_sensor_count} sensors</small></th>
              <td>{formatInterval(point)}</td>
              <td>{unavailableNumber(point.energy_kwh, 6)} kWh</td>
              <td>{unavailableNumber(point.average_power_w, 2)} / {unavailableNumber(point.peak_power_w, 2)} W</td>
              <td>{point.tou_period ?? 'Unavailable'}{point.mixed_rates && <small>Mixed rates</small>}{point.rate_contributions.some((part) => part.tier_name) && <small>{point.rate_contributions.map((part) => `${part.tier_name}: cycle ${part.cumulative_start_kwh}-${part.cumulative_end_kwh} kWh`).join(' / ')}</small>}</td>
              <td>{point.rate_per_kwh ? `${formatMoney(point.rate_per_kwh)}/kWh` : 'Unavailable'}</td>
              <td>{unavailableMoney(point.energy_cost)}</td>
              <td>{point.rate_plan_name ?? 'Unavailable'}<small>{point.rate_version_id ?? 'No version'}</small></td>
              <td>{formatNumber(point.coverage_percent, 2)}%<small>{point.quality_flags.length ? point.quality_flags.join(', ') : 'Complete'}</small></td>
            </tr>))}</tbody>
          </table></div>
          <nav className="history-pagination" aria-label="History table pages"><button className="button secondary compact" disabled={page <= 1} onClick={() => { setPage((current) => Math.max(1, current - 1)) }}><ChevronLeft size={16} /> Previous</button><span>Page {queryData.page} · {queryData.total_buckets} total buckets</span><button className="button secondary compact" disabled={!queryData.next_page} onClick={() => { setPage(queryData.next_page ?? page) }} >Next <ChevronRight size={16} /></button></nav>
        </Panel>}
        {basePoints.length > 0 && costUnavailable && <p className="history-cost-disclosure"><Info size={16} /> Estimated energy cost is unavailable for intervals without a historically effective rate or a completed tier recalculation. Electrical measurements remain visible; the server never guesses a price or resets cumulative tier usage per interval. <Link to="/admin?tab=sites-accounts">Configure or recalculate account</Link></p>}
        {basePoints.length > 0 && <p className="history-cost-disclosure"><Info size={16} /> Estimated energy cost covers interval energy charges for the selected sensors. It excludes account-level service charges, taxes, credits, and other whole-bill items by default.</p>}
      </>}
    </>
  )
}
