import { useQuery } from '@tanstack/react-query'
import { CategoryScale, Chart as ChartJS, Filler, LinearScale, LineElement, PointElement, TimeScale, Tooltip } from 'chart.js'
import { Line } from 'react-chartjs-2'
import { CalendarRange, Download, Info, SlidersHorizontal } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api'
import type { Device } from '../types'
import { EmptyState, ErrorState, formatNumber, LoadingState, PageTitle, Panel } from '../components/UI'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, TimeScale, Tooltip, Filler)
interface History { points: Array<{ timestamp: string; power_w?: string; energy_wh?: string; voltage_v?: string; current_a?: string; power_factor?: string; frequency_hz?: string; quality_flags: string[] }>; missing_ranges: Array<{ start_sequence: number; end_sequence: number }>; coverage_percent: string; next_cursor?: string }

export function HistoryPage() {
  const [search] = useSearchParams()
  const devices = useQuery({ queryKey: ['devices'], queryFn: () => api<Device[]>('/api/v1/devices') })
  const [deviceId, setDeviceId] = useState(search.get('device_id') ?? '')
  const [rangeHours, setRangeHours] = useState(24)
  const [metric, setMetric] = useState('power_w')
  const effectiveDevice = deviceId || devices.data?.[0]?.id || ''
  const end = useMemo(() => new Date(), [])
  const start = useMemo(() => new Date(end.getTime() - rangeHours * 3_600_000), [end, rangeHours])
  const history = useQuery({
    queryKey: ['history', effectiveDevice, rangeHours],
    enabled: Boolean(effectiveDevice),
    queryFn: () => api<History>(`/api/v1/readings/history?device_id=${encodeURIComponent(effectiveDevice)}&start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}&resolution=raw`),
  })
  const labels = history.data?.points.map((point) => new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(new Date(point.timestamp))) ?? []
  const values = history.data?.points.map((point) => Number(point[metric as keyof typeof point] ?? 0)) ?? []
  return (
    <>
      <PageTitle eyebrow="Measurement explorer" title="History & comparison" description="Raw UTC intervals are rendered in your locale, with gaps and quality limitations kept visible." actions={<button className="button secondary"><Download size={17} /> Export</button>} />
      <Panel className="history-panel">
        <div className="history-controls"><label><span><SlidersHorizontal size={15} /> Sensor</span><select value={effectiveDevice} onChange={(event) => { setDeviceId(event.target.value); }}>{devices.data?.map((device) => <option key={device.id} value={device.id}>{device.name}</option>)}</select></label><label><span><CalendarRange size={15} /> Range</span><select value={rangeHours} onChange={(event) => { setRangeHours(Number(event.target.value)); }}><option value={6}>Last 6 hours</option><option value={24}>Last 24 hours</option><option value={168}>Last 7 days</option><option value={720}>Last 30 days</option></select></label><label><span>Metric</span><select value={metric} onChange={(event) => { setMetric(event.target.value); }}><option value="power_w">Power (W)</option><option value="voltage_v">Voltage (V)</option><option value="current_a">Current (A)</option><option value="power_factor">Power factor</option><option value="frequency_hz">Frequency (Hz)</option><option value="energy_wh">Energy (Wh)</option></select></label></div>
        {history.isLoading ? <LoadingState label="Querying raw intervals…" /> : history.error ? <ErrorState error={history.error} retry={() => void history.refetch()} /> : history.data?.points.length ? <div className="history-chart"><Line data={{ labels, datasets: [{ label: metric.replace('_', ' '), data: values, borderColor: '#31d9a3', backgroundColor: 'rgba(49,217,163,.13)', fill: true, pointRadius: 0, tension: 0.25 }] }} options={{ responsive: true, maintainAspectRatio: false, interaction: { intersect: false, mode: 'index' }, scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 8, color: '#78909d' } }, y: { grid: { color: 'rgba(120,144,157,.13)' }, ticks: { color: '#78909d' } } }, plugins: { tooltip: { callbacks: { label: (context) => `${formatNumber(context.parsed.y, 3)} ${metric === 'power_w' ? 'W' : ''}` } } } }} /></div> : <EmptyState title="No readings in this range" message="History appears after durable records have synchronized from device microSD storage." />}
        <div className="coverage-row"><span><Info size={15} /> Data coverage</span><strong>{formatNumber(history.data?.coverage_percent, 2)}%</strong><div className="coverage-bar"><span style={{ width: `${Math.min(100, Number(history.data?.coverage_percent ?? 0))}%` }} /></div></div>
        {(history.data?.missing_ranges.length ?? 0) > 0 && <div className="missing-regions" role="alert"><strong>Missing sequence regions</strong>{history.data?.missing_ranges.map((gap) => <span key={`${gap.start_sequence}-${gap.end_sequence}`}>{gap.start_sequence}–{gap.end_sequence}</span>)}</div>}
      </Panel>
    </>
  )
}
