import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  ArrowRight,
  BatteryCharging,
  Check,
  Clock3,
  FlaskConical,
  RadioTower,
  ReceiptText,
  ShieldCheck,
  Upload,
  Zap,
} from 'lucide-react'
import { Link } from '../../app/router'
import { hasAnyPermission, hasPermission } from '../../access/permissions'
import { adaptHistory } from '../../api/adapters'
import { json, request } from '../../api/client'
import { EnergyChart } from '../../components/charts/EnergyChart'
import { Metric, StatusDot, Surface } from '../../components/data-display/Surface'
import { EmptyState, ErrorState, LoadingState } from '../../components/feedback/States'
import { Page, PageHeader, StatGrid } from '../../components/layout/Layout'
import { HISTORY_REFETCH_INTERVAL_MS, historyPayload } from '../../features/history/historyQuery'
import { ConfigurationStatusChip } from '../../features/configuration/ConfigurationStatusSurface'
import { isCurrentAttentionNotification } from '../../features/alerts/notificationSelectors'
import { SensorHealthEntry } from '../../features/sensors/SensorHealthEntry'
import { useAppearance } from '../../state/AppearanceContext'
import { useLiveHome } from '../../state/LiveHomeContext'
import { useSingleHome } from '../../state/SingleHomeContext'
import { useTestMode } from '../../state/TestModeContext'
import { useAuth } from '../../state/AuthContext'
import type { HistoryFilters } from '../../types/models'
import {
  dateRange,
  energy,
  money,
  power,
  rate,
  statusLabel,
} from '../../utils/format'

const todayFilters: HistoryFilters = { range: 'today', metric: 'energy', scope: 'home' }

export function HomePage() {
  const { resolution } = useSingleHome()
  const { summary, sensors, alerts, cycle, configuration, loading, error, refresh } = useLiveHome()
  const appearance = useAppearance()
  const { session } = useAuth()
  const canEnroll = hasPermission(session, 'enrollment.manage')
  const canManageSensors = hasAnyPermission(session, ['devices.manage', 'topology.manage', 'enrollment.manage'])
  const canManageBills = hasPermission(session, 'utility_bills.manage')
  const testMode = useTestMode()
  const home = resolution?.state === 'ready' ? resolution.home : undefined
  const attentionAlerts = alerts.filter(isCurrentAttentionNotification)
  const dailyHistory = useQuery({
    queryKey: ['history', 'home-daily', home?.id],
    queryFn: () => {
      if (!home) throw new Error('The home is unavailable.')
      return request('/api/v1/history/query', json('POST', historyPayload(todayFilters, home)), adaptHistory)
    },
    enabled: Boolean(home && sensors.length),
    retry: 1,
    refetchInterval: HISTORY_REFETCH_INTERVAL_MS,
  })

  if (loading && !summary) return <LoadingState label="Preparing your home…" />
  if (error && !summary) return <ErrorState error={error} retry={() => void refresh()} />
  if (!summary || !home) return <ErrorState error={new Error('Home summary is unavailable.')} retry={() => void refresh()} />

  if (sensors.length === 0) {
    return (
      <Page className="home-page home-empty-state">
        <PageHeader
          eyebrow="Home overview"
          title={`Good ${dayPart()}, ${firstName(session?.user?.name)}`}
          description={`Finish the two private setup steps for ${home.name}; live readings will appear automatically after the first signed heartbeat.`}
          action={canEnroll && <Link className="button primary" to="/settings/sensors?action=add"><RadioTower size={17} /> Connect sensor</Link>}
        />
        {testMode.state?.enabled && <TestModeHomePreview currency={home.currency} />}
        <StatGrid className="home-status-grid">
          <Metric label="Live data" value="Not connected" identity="home.live_status" detail="Waiting for the first signed heartbeat" />
          <Metric label="Current load" value={power(summary.currentPowerW)} identity="home.current_load" detail="No reporting sensors" />
          <Metric label="Current plan" value={summary.currentPlan ?? 'Not configured'} identity="home.current_plan" detail={summary.currentRate ? rate(summary.currentRate, home.currency) : 'Upload a bill or choose a plan'} />
        </StatGrid>
        <div className="home-onboarding-grid">
          <Surface className="home-primary-onboarding">
            <div className="home-onboarding-icon"><RadioTower /></div>
            <div>
              <span className="section-eyebrow">Step 1 · Live monitoring</span>
              <h2>Connect your first sensor</h2>
              <p>Generate a short-lived setup code, enter it on your ESP32, and this dashboard will begin showing verified whole-home readings.</p>
              <ul className="home-assurance-list">
                <li><ShieldCheck /> Signed device identity</li>
                <li><Clock3 /> Automatic history synchronization</li>
                <li><Check /> No cloud account required</li>
              </ul>
              {canEnroll
                ? <Link className="button primary" to="/settings/sensors?action=add"><RadioTower size={17} /> Connect sensor</Link>
                : <p className="read-only-guidance">No sensors are connected yet. The home owner can add a sensor from Settings.</p>}
            </div>
          </Surface>
          <Surface className="home-setup-card" title="Billing setup" subtitle="Optional until you want cost estimates">
            {summary.currentPlan ? (
              <div className="home-setup-complete">
                <span className="icon-tile"><Check /></span>
                <div><strong>Billing setup complete</strong><p>Your reviewed rate plan is ready before the first reading arrives.</p></div>
                <Link className="text-link" to="/billing">Review billing <ArrowRight /></Link>
              </div>
            ) : (
              <div className="home-setup-complete">
                <span className="icon-tile"><ReceiptText /></span>
                <div><strong>Add your electric rate</strong><p>A reviewed bill can prepare a rate plan and exact billing cycle without changing anything on upload.</p></div>
                {canManageBills && <Link className="button secondary" to="/billing?action=upload"><Upload size={17} /> Upload electric bill</Link>}
              </div>
            )}
          </Surface>
        </div>
        <Surface className="home-ready-note">
          <div>
            <ShieldCheck />
            <span>
              <strong>{configuration?.label ?? 'Configuration status'}</strong>
              <small>{configuration?.summary ?? 'Open configuration status for exact setup guidance.'}</small>
            </span>
            <ConfigurationStatusChip status={configuration} />
          </div>
        </Surface>
        <p className="estimate-disclosure"><AlertTriangle size={16} /> {summary.disclosure}</p>
      </Page>
    )
  }

  return (
    <Page className="home-page home-connected-state">
      <PageHeader
        eyebrow="Home overview"
        title={`Good ${dayPart()}, ${firstName(session?.user?.name)}`}
        description={`Here is what is happening at ${home.name}.`}
        action={<Link className="button secondary" to="/history">View History <ArrowRight size={16} /></Link>}
      />
      {testMode.state?.enabled && <TestModeHomePreview currency={home.currency} />}
      <StatGrid className="home-status-grid">
        <Metric label="Live data" value={summary.hasLiveData ? 'Connected' : 'Waiting'} identity="home.live_status" detail={`${summary.reportingSensors} of ${summary.totalSensors} sensors reporting`} />
        <Metric label="Sensors" value={`${summary.onlineSensors}/${summary.totalSensors}`} identity="home.sensor_status" detail={summary.attentionSensors ? `${summary.attentionSensors} need attention` : 'All sensors reporting'} />
        <Metric label="Current rate" value={rate(summary.currentRate, home.currency)} identity="home.current_rate" detail={summary.currentPeriod ?? summary.currentTier ?? summary.currentPlan ?? 'Plan not configured'} />
        <Metric label="Active alerts" value={summary.activeAlerts} identity="home.active_alerts" detail={summary.activeAlerts ? 'Review recommended' : 'Nothing needs attention'} />
      </StatGrid>

      <div className="home-main-grid">
        <Surface className="power-hero">
          <div className="hero-kicker"><StatusDot state={summary.hasLiveData ? 'live' : 'waiting'} label={summary.hasLiveData ? 'Live power' : 'Waiting for live data'} /></div>
          <div className="power-reading" data-metric-identity="power.current">
            <span className="power-orb"><Zap fill="currentColor" /></span>
            <div><strong>{power(summary.currentPowerW)}</strong><span>right now</span></div>
          </div>
          <StatGrid className="home-power-facts">
            <Metric label="Energy today" value={energy(summary.energyTodayKwh)} identity="energy.today" />
            <Metric label="Estimated today" value={money(summary.estimatedCostToday, home.currency)} identity="cost.today" detail={summary.hasCostData ? 'Current plan applied' : 'Rate data pending'} />
            <Metric label="Recent peak" value={power(summary.recentPeakW)} identity="power.recent_peak" />
          </StatGrid>
        </Surface>
        <div className="home-side-stack">
          {appearance.showSensorsCard && <Surface className="sensor-health-card" title="Sensor health" subtitle={`${summary.onlineSensors} online · ${summary.attentionSensors} need attention`} action={canManageSensors && <Link className="text-link" to="/settings/sensors">Manage <ArrowRight /></Link>}>
            <div className="sensor-peek">
              {sensors.slice(0, 4).map((sensor) => (
                <SensorHealthEntry key={sensor.id} sensor={sensor} serverNow={summary.serverNow} />
              ))}
            </div>
          </Surface>}
          <Surface title="Current pricing" subtitle={summary.currentPlan ?? 'No active rate plan'}>
            {summary.currentPlan ? (
              <div className="rate-now">
                <span className="icon-tile"><BatteryCharging /></span>
                <div><small>{summary.currentTier ? 'Current tier' : 'Current period'}</small><strong>{summary.currentTier ?? summary.currentPeriod ?? 'Flat rate'}</strong><span>{rate(summary.currentRate, home.currency)}</span></div>
              </div>
            ) : (
              <EmptyState compact title="Rate plan needed" message="Upload a reviewed bill or select a published plan to calculate costs." action={canManageBills && <Link className="button secondary compact" to="/billing?action=upload">Set up billing</Link>} />
            )}
            {summary.nextPeriod && <p className="next-rate">Next: {summary.nextPeriod} at {rate(summary.nextRate, home.currency)}</p>}
          </Surface>
        </div>
      </div>

      {summary.currentPlan ? (
        <Surface title="Billing snapshot" subtitle={dateRange(cycle?.startsAt, cycle?.endsAt)} action={<Link className="text-link" to="/billing">Review billing <ArrowRight /></Link>}>
          <StatGrid className="home-billing-grid">
            <Metric label="Cycle usage" value={energy(summary.cycleEnergyKwh)} identity="billing.cycle_energy" />
            <Metric label="Energy charge" value={money(summary.cycleEstimatedCost, home.currency)} identity="billing.energy_charge" />
            <Metric label="Projected bill" value={money(summary.projectedBill, home.currency)} identity="billing.estimate" detail={summary.cycleConfidence ? `${statusLabel(summary.cycleConfidence)} confidence` : undefined} />
            <Metric label="Days remaining" value={cycle?.daysRemaining ?? '—'} identity="billing.days_remaining" />
          </StatGrid>
        </Surface>
      ) : (
        <Surface className="home-billing-callout">
          <EmptyState compact title="Add billing to see energy costs" message="Upload an electric bill to prepare a reviewed rate plan and exact cycle dates." action={canManageBills && <Link className="button primary" to="/billing?action=upload"><ReceiptText size={17} /> Upload electric bill</Link>} />
        </Surface>
      )}

      {appearance.showDailyChart && <Surface
        title="Today’s energy"
        subtitle="Whole-home intervals; missing readings remain visible gaps."
        action={<Link className="text-link" to="/history">Explore History <ArrowRight /></Link>}
      >
        {dailyHistory.isLoading ? <LoadingState label="Loading today’s readings…" /> : dailyHistory.error ? <ErrorState error={dailyHistory.error} retry={() => void dailyHistory.refetch()} /> : dailyHistory.data?.points.length ? (
          <EnergyChart points={dailyHistory.data.points} mode="energy" currency={home.currency} title="Today’s whole-home energy" timezone={dailyHistory.data.timezone} bucket={dailyHistory.data.bucket} rangeStart={dailyHistory.data.rangeStart} rangeEnd={dailyHistory.data.rangeEnd} />
        ) : (
          <EmptyState compact title="Waiting for today’s history" message="Intervals appear after synchronized sensor readings are stored." />
        )}
      </Surface>}

      {attentionAlerts.length > 0 && (
        <Surface title="Needs attention" subtitle="Active household alerts">
          <ul className="actionable-alerts">
            {attentionAlerts.slice(0, 4).map((alert) => (
              <li key={alert.id}><AlertTriangle /><div><strong>{alert.title}</strong><span>{alert.message}</span></div></li>
            ))}
          </ul>
        </Surface>
      )}
      <p className="estimate-disclosure"><AlertTriangle size={16} /> {summary.disclosure}</p>
    </Page>
  )
}

function TestModeHomePreview({ currency }: { currency: string }) {
  const { state } = useTestMode()
  if (!state?.enabled) return null
  return (
    <Surface className="test-mode-surface active">
      <div className="test-mode-inline-heading">
        <FlaskConical />
        <span><strong>Sensor Test Mode preview</strong><small>Synthetic only · real Home data remains unchanged</small></span>
        <span className="pill warning">Test Mode</span>
      </div>
      <StatGrid className="test-mode-summary">
        <Metric label="Simulated load" value={power(state.currentPowerW)} identity="test_mode.home.load" />
        <Metric label="Test energy" value={energy(state.totalEnergyKwh)} identity="test_mode.home.energy" />
        <Metric label="Test sensors" value={`${state.onlineSensors}/${state.sensorCount}`} identity="test_mode.home.sensors" />
        <Metric
          label="Temporary cost preview"
          value={state.costPreview?.available ? money(state.costPreview.estimatedEnergyCost, state.costPreview.currency ?? currency) : 'Off'}
          identity="test_mode.home.cost"
          detail="Never saved to bills or finalized costs"
        />
      </StatGrid>
    </Surface>
  )
}

function dayPart(): string {
  const hour = new Date().getHours()
  return hour < 12 ? 'morning' : hour < 18 ? 'afternoon' : 'evening'
}

function firstName(displayName: string | undefined): string {
  return displayName?.trim().split(/\s+/)[0] || 'there'
}
