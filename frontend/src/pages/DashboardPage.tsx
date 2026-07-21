import { useQuery } from '@tanstack/react-query'
import { ArcElement, Chart as ChartJS, Legend, Tooltip } from 'chart.js'
import { Doughnut } from 'react-chartjs-2'
import {
  ArrowUpRight,
  Clock3,
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
  formatNumber,
  formatTime,
  LoadingState,
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

      <section className="hero-grid hero-grid-single" aria-label="Live power overview">
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

      </section>

      <div className="dashboard-grid dashboard-grid-single">
        <Panel eyebrow="Live composition" title="Device contribution" className="chart-panel">
          {activeDevices.length ? (
            <div className="donut-wrap">
              <Doughnut data={chartData} options={{ responsive: true, maintainAspectRatio: false, cutout: '73%', plugins: { legend: { position: 'bottom', labels: { usePointStyle: true, color: '#68766f', padding: 18 } } } }} />
              <div className="donut-total"><Zap size={18} /><strong>{formatNumber(data.current_load_w)} W</strong><span>now</span></div>
            </div>
          ) : <EmptyState title="Waiting for live power" message="Included devices will appear after their first signed heartbeat." />}
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
