import { useQuery } from '@tanstack/react-query'
import { ArcElement, Chart as ChartJS, Legend, Tooltip } from 'chart.js'
import { Doughnut } from 'react-chartjs-2'
import {
  ArrowUpRight,
  BatteryCharging,
  BellRing,
  CircleDollarSign,
  Clock3,
  Gauge,
  RadioTower,
  RefreshCw,
  Zap,
} from 'lucide-react'
import type { CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useLiveEvents } from '../hooks/useLiveEvents'
import type { Device, FleetSummary } from '../types'
import {
  Disclosure,
  EmptyState,
  ErrorState,
  formatMoney,
  formatNumber,
  formatTime,
  LoadingState,
  Metric,
  PageTitle,
  Panel,
  StatusPill,
} from '../components/UI'

ChartJS.register(ArcElement, Tooltip, Legend)

export function DashboardPage({ canEnroll = false }: { canEnroll?: boolean }) {
  useLiveEvents()
  const summary = useQuery({ queryKey: ['fleet'], queryFn: () => api<FleetSummary>('/api/v1/fleet/summary') })
  const devices = useQuery({ queryKey: ['devices'], queryFn: () => api<Device[]>('/api/v1/devices') })
  if (summary.isLoading || devices.isLoading) return <LoadingState label="Opening the live fleet view…" />
  if (summary.error || devices.error) return <ErrorState error={summary.error ?? devices.error} retry={() => { void summary.refetch(); void devices.refetch() }} />
  const data = summary.data
  if (!data) return <ErrorState error={new Error('Fleet summary was empty')} retry={() => { void summary.refetch() }} />

  const activeDevices = (devices.data ?? []).filter((device) => Number(device.current_watts ?? 0) > 0)
  const onlinePercent = data.total_devices ? Math.round((data.online_devices / data.total_devices) * 100) : 0
  const chartData = {
    labels: activeDevices.map((device) => device.name),
    datasets: [{
      data: activeDevices.map((device) => Number(device.current_watts ?? 0)),
      backgroundColor: ['#57c6a3', '#3b82a0', '#e4a84a', '#8f78bd', '#d76a70', '#63a9a1'],
      borderWidth: 0,
      hoverOffset: 6,
    }],
  }

  return (
    <>
      <PageTitle
        eyebrow="At a glance"
        title="Power Dashboard"
        description="Live energy, device health, synchronization, and estimated Southern California Edison costs across your monitored circuits."
        actions={canEnroll ? <Link className="button primary" to="/enrollment">Enroll devices <ArrowUpRight size={17} /></Link> : undefined}
      />

      <aside className="fleet-scope">
        <span><RadioTower size={18} /></span>
        <p><strong>Monitoring {data.total_devices} enrolled {data.total_devices === 1 ? 'sensor' : 'sensors'}</strong><small>Totals include only explicitly configured circuits; signed heartbeats are the source of live device status.</small></p>
        <StatusPill status={data.online_devices === data.total_devices && data.total_devices > 0 ? 'healthy' : 'pending'} label={`${data.online_devices} online`} />
      </aside>

      <section className="hero-grid" aria-label="Live power overview">
        <article className="power-hero">
          <header><span className="live-pulse" /> Live power</header>
          <div className="power-hero-body">
            <div className="power-ring" style={{ '--progress': `${onlinePercent * 3.6}deg` } as CSSProperties}>
              <div><Zap fill="currentColor" /><strong>{formatNumber(data.current_load_w)}</strong><span>watts now</span></div>
            </div>
            <div className="power-hero-copy">
              <strong>{onlinePercent}%</strong>
              <small>{data.online_devices} of {data.total_devices} devices reporting</small>
            </div>
          </div>
          <footer><span>Recent peak</span><strong>{formatNumber(data.recent_peak_w)} W</strong><Link to="/history">View history <ArrowUpRight size={14} /></Link></footer>
        </article>

        <div className="compact-metric-grid">
          <article><span className="compact-icon mint"><BatteryCharging /></span><p><small>Energy today</small><strong>{formatNumber(data.energy_today_kwh, 2)} <em>kWh</em></strong><span>Since local midnight</span></p></article>
          <article><span className="compact-icon blue"><CircleDollarSign /></span><p><small>Estimated today</small><strong>{formatMoney(data.estimated_cost_today)}</strong><span>{data.current_tou_bucket ?? 'Rate plan pending'}</span></p></article>
          <article><span className="compact-icon amber"><Gauge /></span><p><small>Recent peak</small><strong>{formatNumber(data.recent_peak_w)} <em>W</em></strong><span>Selected aggregate</span></p></article>
          <article><span className="compact-icon violet"><RefreshCw /></span><p><small>Synchronized</small><strong>{data.synchronized_devices}/{data.total_devices}</strong><span>Historical backlog clear</span></p></article>
        </div>
      </section>

      <section className="metric-grid metric-grid-4" aria-label="Fleet metrics">
        <Metric label="Energy today" value={formatNumber(data.energy_today_kwh, 2)} unit="kWh" detail="Monitored energy" />
        <Metric label="Estimated today" value={formatMoney(data.estimated_cost_today)} detail="Configured SCE rate" />
        <Metric label="Billing cycle" value={formatNumber(data.billing_cycle_energy_kwh, 1)} unit="kWh" detail="Current cycle" />
        <Metric label="Cycle estimate" value={formatMoney(data.estimated_billing_cycle_cost)} detail="Not a utility bill" />
        <Metric label="Recent peak" value={formatNumber(data.recent_peak_w)} unit="W" detail="Selected aggregate" />
        <Metric label="Devices online" value={`${data.online_devices}/${data.total_devices}`} detail="Signed heartbeat status" />
        <Metric label="Synchronized" value={data.synchronized_devices} detail="No historical backlog" />
        <Metric label="Active alerts" value={data.active_alerts} detail={data.active_alerts ? 'Review recommended' : 'No open alerts'} />
      </section>

      <div className="dashboard-grid">
        <Panel eyebrow="Live composition" title="Device contribution" className="chart-panel">
          {activeDevices.length ? (
            <div className="donut-wrap">
              <Doughnut data={chartData} options={{ responsive: true, maintainAspectRatio: false, cutout: '73%', plugins: { legend: { position: 'bottom', labels: { usePointStyle: true, color: '#68766f', padding: 18 } } } }} />
              <div className="donut-total"><Zap size={18} /><strong>{formatNumber(data.current_load_w)} W</strong><span>now</span></div>
            </div>
          ) : <EmptyState title="Waiting for live power" message="Included devices will appear after their first signed heartbeat." />}
        </Panel>
        <Panel eyebrow="Fleet state" title="Operational pulse">
          <div className="pulse-list">
            <div><span className="pulse-icon"><RadioTower /></span><p><strong>{data.online_devices} sensors online</strong><small>{data.total_devices - data.online_devices} offline or stale</small></p><StatusPill status={data.online_devices === data.total_devices ? 'healthy' : 'pending'} label="Heartbeat" /></div>
            <div><span className="pulse-icon"><BatteryCharging /></span><p><strong>{data.synchronized_devices} synchronized</strong><small>Sequence cursor has no backlog</small></p><StatusPill status={data.synchronized_devices === data.total_devices ? 'healthy' : 'pending'} label="Storage" /></div>
            <div><span className="pulse-icon"><CircleDollarSign /></span><p><strong>{data.current_tou_bucket ?? 'Rate not assigned'}</strong><small>Current local TOU bucket</small></p><Link to="/rates">Inspect</Link></div>
            <div><span className="pulse-icon"><BellRing /></span><p><strong>{data.active_alerts} active alerts</strong><small>Health and synchronization evidence</small></p><Link to="/alerts">Review</Link></div>
          </div>
        </Panel>
      </div>

      <Panel eyebrow="Sensor fleet" title="Circuits right now" actions={<Link to="/devices">All devices <ArrowUpRight size={16} /></Link>}>
        {(devices.data?.length ?? 0) === 0 ? (
          <EmptyState title="No sensors enrolled" message="Create short-lived enrollment tokens to bring ESP32-S3 sensors online." action={canEnroll ? <Link className="button secondary" to="/enrollment">Start enrollment</Link> : undefined} />
        ) : (
          <div className="device-card-grid">
            {devices.data?.slice(0, 8).map((device) => (
              <Link className="device-card" to={`/devices/${device.id}`} key={device.id}>
                <div className="device-card-head"><span className="device-icon"><Zap /></span><StatusPill status={device.status} /></div>
                <h3>{device.name}</h3><p>{device.measurement_role} · {device.connection_mode}</p>
                <strong className="device-watts">{formatNumber(device.current_watts)} <small>W</small></strong>
                <div className="device-indicators">
                  <span className={device.pzem_ok ? 'ok' : 'bad'}>PZEM</span><span className={device.sd_ok ? 'ok' : 'bad'}>SD</span><span className={device.time_trusted ? 'ok' : 'bad'}>TIME</span><span className={device.backlog ? 'warn' : 'ok'}>SYNC</span>
                </div>
                <small className="last-seen"><Clock3 size={13} /> {formatTime(device.last_seen_at)}</small>
              </Link>
            ))}
          </div>
        )}
      </Panel>
      <Disclosure />
    </>
  )
}
