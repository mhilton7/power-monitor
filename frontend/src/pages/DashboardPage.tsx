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
import { CanonicalAction } from '../actions'
import { api } from '../api'
import { formatCurrency, formatEnergyRate } from '../formatters'
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
import type { Device, FleetSummary, TierStatus, UtilityAccount } from '../types'

ChartJS.register(ArcElement, Tooltip, Legend)

export function DashboardPage({ canEnroll = false }: { canEnroll?: boolean }) {
  useLiveEvents()
  const siteId = useSelectedSiteId()
  const status = useStatusIndicators()
  const fleetUrl = siteId ? `/api/v1/fleet/summary?site_id=${encodeURIComponent(siteId)}` : '/api/v1/fleet/summary'
  const summary = useQuery({ queryKey: ['fleet', siteId], queryFn: () => api<FleetSummary>(fleetUrl) })
  const devices = useQuery({ queryKey: ['devices'], queryFn: () => api<Device[]>('/api/v1/devices') })
  const accounts = useQuery({ queryKey: ['utility-accounts', 'overview', siteId], queryFn: () => api<UtilityAccount[]>('/api/v1/utility-accounts') })
  const account = accounts.data?.find((item) => (!siteId || item.site_id === siteId) && item.status === 'active')
  const tierStatus = useQuery({
    queryKey: ['tier-status', account?.id],
    queryFn: () => api<TierStatus>(`/api/v1/utility-accounts/${account?.id}/tier-status`),
    enabled: Boolean(account),
    refetchInterval: 60_000,
  })
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
  const peakIsConfiguredElsewhere = Boolean(status.layout?.zones.some((zone) => zone.items.some((item) => item.definition?.metric_identity === 'power.recent_peak')))
  const backlog = siteDevices.reduce((total, device) => total + device.backlog, 0)
  const hardwareIssues = siteDevices.filter((device) => device.pzem_ok === false || device.sd_ok === false)
  const operationalIssues = data.online_devices < data.total_devices || backlog > 0 || hardwareIssues.length > 0 || !data.rate_configured
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
        actions={canEnroll && data.total_devices > 0 ? <CanonicalAction id="device.enroll" surface="page"><Link className="button primary" to="/monitoring/enrollment">Enroll devices <ArrowUpRight size={17} /></Link></CanonicalAction> : undefined}
      />
      <StatusIndicatorZone zone="overview_summary" />

      {data.total_devices === 0 ? (
        <Panel className="overview-onboarding">
          <EmptyState
            title="No sensors enrolled"
            message="Enroll an ESP32 sensor to begin. Readings and site summaries appear after its first valid signed heartbeat."
            action={<div className="inline-actions">{canEnroll ? <CanonicalAction id="device.enroll" surface="contextual_link"><Link className="button primary" to="/monitoring/enrollment">Enroll devices <ArrowUpRight size={16} /></Link></CanonicalAction> : <Link className="button secondary" to="/monitoring/devices">Open Devices</Link>}{!data.rate_configured && <Link className="button secondary" to="/billing/accounts">Configure utility account <ArrowUpRight size={16} /></Link>}</div>}
          />
          {data.rate_configured && <dl className="onboarding-rate-context"><div><dt>Current rate plan</dt><dd>{data.current_rate_plan} · v{data.current_rate_version}</dd></div><div><dt>Current rate period</dt><dd>{data.current_tou_bucket ?? 'Account usage required'}</dd></div><div><dt>Current energy price</dt><dd>{data.current_rate_price_per_kwh ? formatEnergyRate(data.current_rate_price_per_kwh) : 'Account tier unavailable'}</dd></div></dl>}
        </Panel>
      ) : <>
        <section className="overview-site-state" aria-label="Current site state">
          <div><Radio aria-hidden="true" /><span><strong>Monitoring {data.total_devices} {data.total_devices === 1 ? 'sensor' : 'sensors'}</strong><small>Selected site aggregate</small></span></div>
        </section>

        {!hasLiveData && <Panel className="overview-waiting">
          <EmptyState
            title="Waiting for sensor data"
            message="The enrolled sensors have not sent a valid signed heartbeat yet. Keep this page open or review device setup and connectivity."
            action={<Link className="button secondary" to="/monitoring/devices">Review devices</Link>}
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
              <Link to="/analytics/history">View history <ArrowUpRight size={14} /></Link>
            </footer>
          </article>
        </section>

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
              {data.online_devices < data.total_devices && <Link to="/monitoring/devices"><AlertTriangle /><span><strong>Some devices need attention</strong><small>{data.total_devices - data.online_devices} offline or stale</small></span><ArrowUpRight /></Link>}
              {backlog > 0 && <Link to="/monitoring/devices"><RefreshCw /><span><strong>Historical readings are synchronizing</strong><small>{backlog} readings reported in the backlog</small></span><ArrowUpRight /></Link>}
              {hardwareIssues.length > 0 && <Link to="/monitoring/devices"><AlertTriangle /><span><strong>Sensor hardware issue</strong><small>{hardwareIssues.length} devices report meter or storage trouble</small></span><ArrowUpRight /></Link>}
              {!data.rate_configured && <Link to="/billing/accounts"><Clock3 /><span><strong>No effective utility rate</strong><small>Configure the utility account and assign a published rate.</small></span><ArrowUpRight /></Link>}
            </div>}
          </Panel>
        </div>

        <Panel eyebrow="Sensor fleet" title="Circuits right now" actions={<Link to="/monitoring/devices">All devices <ArrowUpRight size={16} /></Link>}>
          <div className="device-card-grid">
            {siteDevices.slice(0, 8).map((device) => (
              <Link className="device-card" to={`/monitoring/devices/${device.id}`} key={device.id}>
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
      {tierStatus.data?.available && <Panel eyebrow="Current billing cycle" title="Tier progress" className="overview-tier-summary" actions={<Link to="/analytics/usage">View usage <ArrowUpRight size={14} /></Link>}>
        <dl className="overview-tier-grid">
          <div><dt>Current tier</dt><dd>{tierStatus.data.current_tier?.name ?? 'Unavailable'}</dd><small>{tierStatus.data.remaining_kwh ? `${formatNumber(tierStatus.data.remaining_kwh)} kWh to next tier` : 'Highest configured tier'}</small></div>
          <div><dt>Cycle usage</dt><dd>{formatNumber(tierStatus.data.authoritative_usage_kwh)} kWh</dd><small>{tierStatus.data.cycle.days_remaining} days remaining</small></div>
          <div><dt>Energy charge</dt><dd>{formatCurrency(tierStatus.data.energy_charge)}</dd><small>Chronological tier allocation</small></div>
          <div><dt>Projected cycle</dt><dd>{formatNumber(tierStatus.data.projected_usage_kwh)} kWh</dd><small>{tierStatus.data.projected_final_tier?.name ?? 'Tier unavailable'} / {tierStatus.data.projection_confidence} confidence</small></div>
        </dl>
      </Panel>}
      <Disclosure />
    </>
  )
}
