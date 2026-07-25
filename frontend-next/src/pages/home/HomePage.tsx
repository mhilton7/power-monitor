import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  ArrowRight,
  BatteryCharging,
  RadioTower,
  ReceiptText,
  Upload,
  Zap,
} from 'lucide-react'
import { Link } from '../../app/router'
import { adaptHistory } from '../../api/adapters'
import { json, request } from '../../api/client'
import { EnergyChart } from '../../components/charts/EnergyChart'
import { Metric, StatusDot, Surface } from '../../components/data-display/Surface'
import { EmptyState, ErrorState, LoadingState } from '../../components/feedback/States'
import { historyPayload } from '../../features/history/historyQuery'
import { useLiveHome } from '../../state/LiveHomeContext'
import { useSingleHome } from '../../state/SingleHomeContext'
import { useAppearance } from '../../state/AppearanceContext'
import type { HistoryFilters } from '../../types/models'
import { dateRange, energy, money, power, rate, relativeTime, statusLabel } from '../../utils/format'

const todayFilters: HistoryFilters = { range: 'today', metric: 'energy', scope: 'home' }

export function HomePage() {
  const { resolution } = useSingleHome()
  const { summary, sensors, alerts, cycle, loading, error, refresh } = useLiveHome()
  const appearance = useAppearance()
  const home = resolution?.state === 'ready' ? resolution.home : undefined
  const dailyHistory = useQuery({
    queryKey: ['history', 'home-daily', home?.id],
    queryFn: () => {
      if (!home) throw new Error('The home is unavailable.')
      return request('/api/v1/history/query', json('POST', historyPayload(todayFilters, home)), adaptHistory)
    },
    enabled: Boolean(home && sensors.length),
    retry: 1,
  })

  if (loading && !summary) return <LoadingState label="Preparing your home…" />
  if (error && !summary) return <ErrorState error={error} retry={() => void refresh()} />
  if (!summary || !home) return <ErrorState error={new Error('Home summary is unavailable.')} retry={() => void refresh()} />

  if (sensors.length === 0) {
    return (
      <div className="page-stack home-page">
        <PageHeading title={`Good ${dayPart()}, ${firstName()}`} description="Your private home energy dashboard is ready." />
        <Surface className="hero-empty">
          <EmptyState
            title="Connect your first sensor"
            message="Generate a short-lived setup code, enter it on your ESP32, and readings will appear here after its first signed heartbeat."
            action={<Link className="button primary" to="/settings/sensors?action=add"><RadioTower size={17} /> Connect sensor</Link>}
          />
        </Surface>
        {!summary.currentPlan && (
          <Surface className="bill-empty">
            <EmptyState
              title="Upload your electric bill"
              message="A reviewed bill can prepare your rate plan and billing cycle without changing anything automatically."
              action={<Link className="button secondary" to="/billing?action=upload"><Upload size={17} /> Upload bill</Link>}
            />
          </Surface>
        )}
      </div>
    )
  }

  return (
    <div className="page-stack home-page">
      <PageHeading title={`Good ${dayPart()}, ${firstName()}`} description={`Here is what is happening at ${home.name}.`} />
      <div className="home-hero-grid">
        <Surface className="power-hero">
          <div className="hero-kicker"><StatusDot state={summary.hasLiveData ? 'live' : 'waiting'} label={summary.hasLiveData ? 'Live power' : 'Waiting for live data'} /></div>
          <div className="power-reading" data-metric-identity="power.current">
            <span className="power-orb"><Zap fill="currentColor" /></span>
            <div><strong>{power(summary.currentPowerW)}</strong><span>right now</span></div>
          </div>
          <div className="hero-facts">
            <span><small>Sensors reporting</small><strong>{summary.reportingSensors} of {summary.totalSensors}</strong></span>
            <span data-metric-identity="power.recent_peak"><small>Recent peak</small><strong>{power(summary.recentPeakW)}</strong></span>
            <span><small>Freshness</small><strong>{relativeTime(summary.latestDataAt)}</strong></span>
          </div>
          <Link className="text-link" to="/history">View History <ArrowRight /></Link>
        </Surface>
        <div className="home-summary-column">
          <Surface title="Today" subtitle="Since local midnight">
            <div className="metric-row">
              <Metric label="Energy" value={energy(summary.energyTodayKwh)} identity="energy.today" />
              <Metric label="Estimated cost" value={money(summary.estimatedCostToday, home.currency)} identity="cost.today" detail={summary.hasCostData ? 'Using your current plan' : 'Rate data pending'} />
              <Metric label="Compared with yesterday" value="—" identity="energy.yesterday_delta" detail="Available after two complete days" />
            </div>
          </Surface>
          {appearance.showSensorsCard && <Surface title="Sensors" subtitle={`${summary.onlineSensors} online · ${summary.attentionSensors} need attention`} action={<Link className="text-link" to="/settings/sensors">Manage sensors <ArrowRight /></Link>}>
            <div className="sensor-peek">
              {sensors.slice(0, 3).map((sensor) => (
                <div key={sensor.id}><StatusDot state={sensor.online ? 'live' : 'attention'} label={sensor.name} /><strong>{power(sensor.currentPowerW)}</strong></div>
              ))}
            </div>
          </Surface>}
        </div>
      </div>

      {!summary.currentPlan ? (
        <Surface className="bill-empty">
          <EmptyState
            title="Upload your electric bill"
            message="Add a reviewed rate plan to see current pricing and better bill estimates."
            action={<Link className="button primary" to="/billing?action=upload"><ReceiptText size={17} /> Upload electric bill</Link>}
          />
        </Surface>
      ) : (
        <div className="home-secondary-grid">
          <Surface title="Billing cycle" subtitle={dateRange(cycle?.startsAt, cycle?.endsAt)}>
            <div className="metric-row compact">
              <Metric label="Usage" value={energy(summary.cycleEnergyKwh)} identity="billing.cycle_energy" />
              <Metric label="Energy charge" value={money(summary.cycleEstimatedCost, home.currency)} identity="billing.energy_charge" />
              <Metric label="Projected bill" value={money(summary.projectedBill, home.currency)} identity="billing.estimate" detail={summary.cycleConfidence ? `${statusLabel(summary.cycleConfidence)} confidence` : undefined} />
              <Metric label="Days remaining" value={cycle?.daysRemaining ?? '—'} identity="billing.days_remaining" />
            </div>
          </Surface>
          <Surface title="Current rate" subtitle={summary.currentPlan}>
            <div className="rate-now">
              <span className="icon-tile"><BatteryCharging /></span>
              <div>
                <small>{summary.currentTier ? 'Current tier' : 'Current period'}</small>
                <strong>{summary.currentTier ?? summary.currentPeriod ?? 'Flat rate'}</strong>
                <span>{rate(summary.currentRate, home.currency)}</span>
              </div>
            </div>
            {summary.remainingTierKwh && <div className="progress-line"><span style={{ width: `${Math.min(100, Number(summary.tierProgressPercent ?? 0))}%` }} /><small>{energy(summary.remainingTierKwh)} remaining in this tier</small></div>}
            {summary.nextPeriod && <p className="next-rate">Next: {summary.nextPeriod} at {rate(summary.nextRate, home.currency)}</p>}
            <Link className="text-link" to="/billing">Review billing <ArrowRight /></Link>
          </Surface>
        </div>
      )}

      {appearance.showDailyChart && <Surface
        title="Today’s energy"
        subtitle="A simple view of whole-home usage. Missing readings remain gaps."
        action={<Link className="text-link" to="/history">Explore History <ArrowRight /></Link>}
      >
        {dailyHistory.isLoading ? <LoadingState label="Loading today’s readings…" /> : dailyHistory.error ? <ErrorState error={dailyHistory.error} retry={() => void dailyHistory.refetch()} /> : dailyHistory.data?.points.length ? (
          <EnergyChart points={dailyHistory.data.points} mode="energy" currency={home.currency} title="Today’s whole-home energy" />
        ) : (
          <EmptyState title="Waiting for today’s history" message="Intervals appear after synchronized sensor readings are stored." />
        )}
      </Surface>}

      {alerts.length > 0 && (
        <Surface title="Needs attention" subtitle="Active household alerts">
          <ul className="actionable-alerts">
            {alerts.slice(0, 4).map((alert) => (
              <li key={alert.id}><AlertTriangle /><div><strong>{alert.title}</strong><span>{alert.message}</span></div></li>
            ))}
          </ul>
        </Surface>
      )}
      <p className="estimate-disclosure"><AlertTriangle size={16} /> {summary.disclosure}</p>
    </div>
  )
}

function PageHeading({ title, description }: { title: string; description: string }) {
  return <header className="page-heading"><div><h1>{title}</h1><p>{description}</p></div></header>
}

function dayPart(): string {
  const hour = new Date().getHours()
  return hour < 12 ? 'morning' : hour < 18 ? 'afternoon' : 'evening'
}

function firstName(): string {
  const fallback = 'there'
  try {
    const stored = document.querySelector('.user-button strong')?.textContent?.trim()
    return stored?.split(/\s+/)[0] ?? fallback
  } catch {
    return fallback
  }
}
