import { useQuery } from '@tanstack/react-query'
import { ArcElement, Chart as ChartJS, Legend, Tooltip } from 'chart.js'
import { Doughnut } from 'react-chartjs-2'
import { ArrowUpRight, BatteryCharging, CircleDollarSign, Clock3, Gauge, RadioTower, Zap } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useLiveEvents } from '../hooks/useLiveEvents'
import type { Device, FleetSummary } from '../types'
import { Disclosure, EmptyState, ErrorState, formatMoney, formatNumber, formatTime, LoadingState, Metric, PageTitle, Panel, StatusPill } from '../components/UI'

ChartJS.register(ArcElement, Tooltip, Legend)

export function DashboardPage({ canOperate = false }: { canOperate?: boolean }) {
  useLiveEvents()
  const summary = useQuery({ queryKey: ['fleet'], queryFn: () => api<FleetSummary>('/api/v1/fleet/summary') })
  const devices = useQuery({ queryKey: ['devices'], queryFn: () => api<Device[]>('/api/v1/devices') })
  if (summary.isLoading || devices.isLoading) return <LoadingState label="Opening the live fleet view…" />
  if (summary.error || devices.error) return <ErrorState error={summary.error ?? devices.error} retry={() => { void summary.refetch(); void devices.refetch() }} />
  const data = summary.data
  if (!data) return <ErrorState error={new Error('Fleet summary was empty')} retry={() => { void summary.refetch() }} />
  const activeDevices = (devices.data ?? []).filter((device) => Number(device.current_watts ?? 0) > 0)
  const chartData = {
    labels: activeDevices.map((device) => device.name),
    datasets: [{
      data: activeDevices.map((device) => Number(device.current_watts ?? 0)),
      backgroundColor: ['#31d9a3', '#5da7ff', '#ffc857', '#ad8cff', '#ff7a90', '#60d6e8'],
      borderWidth: 0,
      hoverOffset: 6,
    }],
  }
  return (
    <>
      <PageTitle
        eyebrow="Fleet overview"
        title="Energy, without the guesswork."
        description="Application health and signed heartbeats drive this view; network ping is supporting evidence only."
        actions={canOperate ? <Link className="button primary" to="/enrollment">Enroll sensor <ArrowUpRight size={17} /></Link> : undefined}
      />
      <section className="metric-grid metric-grid-4">
        <Metric label="Current aggregate" value={formatNumber(data.current_load_w)} unit="W" detail="Explicitly included circuits" />
        <Metric label="Energy today" value={formatNumber(data.energy_today_kwh, 2)} unit="kWh" detail={`${formatMoney(data.estimated_cost_today)} estimated`} />
        <Metric label="Billing cycle" value={formatNumber(data.billing_cycle_energy_kwh, 1)} unit="kWh" detail={`${formatMoney(data.estimated_billing_cycle_cost)} estimated`} />
        <Metric label="Fleet health" value={`${data.online_devices}/${data.total_devices}`} detail={`${data.synchronized_devices} fully synchronized`} />
      </section>
      <div className="dashboard-grid">
        <Panel eyebrow="Live composition" title="Device contribution" className="chart-panel">
          {activeDevices.length ? (
            <div className="donut-wrap">
              <Doughnut data={chartData} options={{ responsive: true, maintainAspectRatio: false, cutout: '73%', plugins: { legend: { position: 'bottom', labels: { usePointStyle: true, color: '#8fa7b5', padding: 18 } } } }} />
              <div className="donut-total"><Zap size={18} /><strong>{formatNumber(data.current_load_w)} W</strong><span>now</span></div>
            </div>
          ) : <EmptyState title="Waiting for live power" message="Included devices will appear after their first signed heartbeat." />}
        </Panel>
        <Panel eyebrow="At a glance" title="Operational pulse">
          <div className="pulse-list">
            <div><span className="pulse-icon"><RadioTower /></span><p><strong>{data.online_devices} sensors online</strong><small>{data.total_devices - data.online_devices} offline or stale</small></p><StatusPill status={data.online_devices === data.total_devices ? 'healthy' : 'pending'} label="Heartbeat" /></div>
            <div><span className="pulse-icon"><BatteryCharging /></span><p><strong>{data.synchronized_devices} synchronized</strong><small>Sequence cursor has no backlog</small></p><StatusPill status={data.synchronized_devices === data.total_devices ? 'healthy' : 'pending'} label="Storage" /></div>
            <div><span className="pulse-icon"><CircleDollarSign /></span><p><strong>{data.current_tou_bucket ?? 'Rate not assigned'}</strong><small>Current local TOU bucket</small></p><Link to="/rates">Inspect</Link></div>
            <div><span className="pulse-icon"><Gauge /></span><p><strong>{formatNumber(data.recent_peak_w)} W recent peak</strong><small>Selected aggregate only</small></p><Link to="/history">History</Link></div>
          </div>
        </Panel>
      </div>
      <Panel eyebrow="Sensor fleet" title="Circuits right now" actions={<Link to="/devices">All devices <ArrowUpRight size={16} /></Link>}>
        {(devices.data?.length ?? 0) === 0 ? (
          <EmptyState title="No sensors enrolled" message="Create a short-lived enrollment token to bring the first ESP32-S3 sensor online." action={canOperate ? <Link className="button secondary" to="/enrollment">Start enrollment</Link> : undefined} />
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
