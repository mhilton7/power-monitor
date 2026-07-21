import { useQuery } from '@tanstack/react-query'
import { ArcElement, Chart as ChartJS, Legend, Tooltip } from 'chart.js'
import { Doughnut } from 'react-chartjs-2'
import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  Clock3,
  Radio,
  RefreshCw,
  Zap,
} from 'lucide-react'
import type { CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { StatusIndicatorZone, useStatusIndicators } from '../components/StatusIndicators'
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
import { useLiveEvents } from '../hooks/useLiveEvents'
import { useSelectedSiteId } from '../hooks/useSelectedSite'
import type { Device, FleetSummary } from '../types'

ChartJS.register(ArcElement, Tooltip, Legend)

export function DashboardPage({ canEnroll = false }: { canEnroll?: boolean }) {
  useLiveEvents()
  const siteId = useSelectedSiteId()
  const status = useStatusIndicators()
  const fleetUrl = siteId ? `/api/v1/fleet/summary?site_id=${encodeURIComponent(siteId)}` : '/api/v1/fleet/summary'
  const summary = useQuery({ queryKey: ['fleet', siteId], queryFn: () => api<FleetSummary>(fleetUrl) })
  const devices = useQuery({ queryKey: ['devices'], queryFn: () => api<Device[]>('/api/v1/devices') })
  if (summary.isLoading || devices.isLoading) return <LoadingState label="Opening the live site view…" />
  if (summary.error || devices.error) return <ErrorState error={summary.error ?? devices.error} retry={() => { void summary.refetch(); void devices.refetch() }} />
  const data = summary.data
  if (!data) return <ErrorState error={new Error('Site summary was empty')} retry={() => { void summary.refetch() }} />

  const siteDevices = (devices.data ?? []).filter((device) => !siteId || device.site_id === siteId)
  const includedDevices = siteDevices.filter((device) => device.included_in_default)
  const reportingDevices = data.reporting_devices ?? siteDevices.filter((device) => device.last_seen_at).length
  const hasLiveData = data.has_live_data
  const onlinePercent = data.total_devices ? Math.round((reportingDevices / data.total_devices) * 100) : 0
  const contributingDevices = includedDevices.filter((device) => Number(device.current_watts ?? 0) > 0)
  const hasSiteSummary = Boolean(status.layout?.zones.some((zone) => zone.key === 'overview_site_summary' && zone.items.length))
  const peakIsConfiguredElsewhere = Boolean(status.layout?.zones.some((zone) => zone.items.some((item) => item.definition?.metric_identity === 'power.recent_peak')))
  const backlog = siteDevices.reduce((total, device) => total + device.backlog, 0)
  const hardwareIssues = siteDevices.filter((device) => device.pzem_ok === false || device.sd_ok === false)
  const operationalIssues = data.online_devices < data.total_devices || backlog > 0 || hardwareIssues.length > 0 || !data.current_tou_bucket
  const chartData = {
    labels: contributingDevices.map((device) => device.name),
    datasets: [{
      data: contributingDevices.map((device) => Number(device.current_watts ?? 0)),
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
        description="Monitor energy use, costs, device status, and site performance in one place."
        actions={canEnroll && data.total_devices > 0 ? <Link className="button primary" to="/enrollment">Enroll devices <ArrowUpRight size={17} /></Link> : undefined}
      />

      {data.total_devices === 0 ? (
        <Panel className="overview-onboarding">
          <EmptyState
            title="No sensors enrolled"
            message="Enroll an ESP32 sensor to begin. Readings and site summaries appear after its first valid signed heartbeat."
            action={canEnroll ? <Link className="button primary" to="/enrollment">Enroll devices <ArrowUpRight size={16} /></Link> : <Link className="button secondary" to="/devices">Open Devices</Link>}
          />
        </Panel>
      ) : <>
        <section className="overview-site-state" aria-label="Current site state">
          <div><Radio aria-hidden="true" /><span><strong>Monitoring {data.total_devices} {data.total_devices === 1 ? 'sensor' : 'sensors'}</strong><small>Selected site aggregate</small></span></div>
          <StatusIndicatorZone zone="overview_site_state" />
          {canEnroll && <Link to="/enrollment">Enroll devices <ArrowUpRight size={14} /></Link>}
        </section>

        {!hasLiveData && <Panel className="overview-waiting">
          <EmptyState
            title="Waiting for sensor data"
            message="The enrolled sensors have not sent a valid signed heartbeat yet. Keep this page open or review device setup and connectivity."
            action={<Link className="button secondary" to="/devices">Review devices</Link>}
          />
        </Panel>}

        <section className="hero-grid hero-grid-single" aria-label="Live power overview">
          <article className={`power-hero ${hasLiveData ? '' : 'power-hero-unavailable'}`.trim()} data-metric-identity="power.current">
            <header><span className={hasLiveData ? 'live-pulse' : 'status-dot'} /> Live power</header>
            <div className="power-hero-body">
              <div className="power-ring" style={{ '--progress': `${onlinePercent * 3.6}deg` } as CSSProperties}>
                <div><Zap fill="currentColor" /><strong>{hasLiveData ? formatNumber(data.current_load_w) : '—'}</strong><span>{hasLiveData ? 'watts now' : 'waiting for data'}</span></div>
              </div>
              <div className="power-hero-copy">
                <strong>{hasLiveData ? `${onlinePercent}%` : 'Waiting'}</strong>
                <small>{reportingDevices} of {data.total_devices} devices reporting</small>
                <small>Latest {formatTime(data.latest_heartbeat_at)}</small>
              </div>
            </div>
            <footer>
              {!peakIsConfiguredElsewhere && <span data-metric-identity="power.recent_peak">Recent peak <strong>{hasLiveData ? `${formatNumber(data.recent_peak_w)} W` : 'Unavailable'}</strong></span>}
              <Link to="/history">View history <ArrowUpRight size={14} /></Link>
            </footer>
          </article>
        </section>

        {hasLiveData && hasSiteSummary && <Panel eyebrow="General site summary" title="Site Summary" className="overview-site-summary">
          <StatusIndicatorZone zone="overview_site_summary" />
        </Panel>}

        <div className="dashboard-grid">
          <Panel eyebrow="Live composition" title="Device contribution" className="chart-panel">
            {contributingDevices.length ? (
              <div className="donut-wrap">
                <Doughnut data={chartData} options={{ responsive: true, maintainAspectRatio: false, cutout: '73%', plugins: { legend: { position: 'bottom', labels: { usePointStyle: true, color: '#68766f', padding: 18 } } } }} />
                <div className="donut-total"><Zap size={18} /><strong>{formatNumber(data.current_load_w)} W</strong><span>now</span></div>
              </div>
            ) : hasLiveData ? <EmptyState title="No current load" message="Reporting sensors currently measure a valid zero-watt total." /> : <EmptyState title="Waiting for live power" message="Included devices appear here after their first signed heartbeat." />}
          </Panel>
          <Panel eyebrow="Selected site" title="Operational status" className="operational-status">
            {!operationalIssues ? <div className="operational-ok"><CheckCircle2 /><span><strong>Site operating normally</strong><small>Devices are reporting, synchronized, and assigned to a rate.</small></span></div> : <div className="operational-list">
              {data.online_devices < data.total_devices && <Link to="/devices"><AlertTriangle /><span><strong>Some devices need attention</strong><small>{data.total_devices - data.online_devices} offline or stale</small></span><ArrowUpRight /></Link>}
              {backlog > 0 && <Link to="/devices"><RefreshCw /><span><strong>Historical readings are synchronizing</strong><small>{backlog} readings reported in the backlog</small></span><ArrowUpRight /></Link>}
              {hardwareIssues.length > 0 && <Link to="/devices"><AlertTriangle /><span><strong>Sensor hardware issue</strong><small>{hardwareIssues.length} devices report meter or storage trouble</small></span><ArrowUpRight /></Link>}
              {!data.current_tou_bucket && <Link to="/rates"><Clock3 /><span><strong>Rate assignment needed</strong><small>Assign a rate to estimate monitored energy costs.</small></span><ArrowUpRight /></Link>}
            </div>}
          </Panel>
        </div>

        <Panel eyebrow="Sensor fleet" title="Circuits right now" actions={<Link to="/devices">All devices <ArrowUpRight size={16} /></Link>}>
          <div className="device-card-grid">
            {siteDevices.slice(0, 8).map((device) => (
              <Link className="device-card" to={`/devices/${device.id}`} key={device.id}>
                <div className="device-card-head"><span className="device-icon"><Zap /></span><StatusPill status={device.status} /></div>
                <h3>{device.name}</h3><p>{device.measurement_role} · {device.connection_mode}</p>
                <strong className="device-watts">{device.last_seen_at ? formatNumber(device.current_watts) : '—'} <small>W</small></strong>
                <div className="device-indicators">
                  <span className={device.pzem_ok ? 'ok' : 'bad'}>PZEM</span><span className={device.sd_ok ? 'ok' : 'bad'}>SD</span><span className={device.time_trusted ? 'ok' : 'bad'}>TIME</span><span className={device.backlog ? 'warn' : 'ok'}>SYNC</span>
                </div>
                <small className="last-seen"><Clock3 size={13} /> {formatTime(device.last_seen_at)}</small>
              </Link>
            ))}
          </div>
        </Panel>
      </>}
      <Disclosure />
    </>
  )
}
