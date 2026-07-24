import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowUpRight, CalendarDays, Gauge, Layers3 } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { EmptyState, ErrorState, LoadingState, PageTitle, Panel, StatusPill } from '../components/UI'
import { useSelectedSiteId } from '../hooks/useSelectedSite'
import type { TierStatus, UtilityAccount } from '../types'

function number(value?: string, digits = 1) {
  if (value === undefined || !Number.isFinite(Number(value))) return 'Unavailable'
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits })
}

function cycleLabel(status: TierStatus) {
  const start = new Date(status.cycle.starts_at).toLocaleDateString()
  const end = new Date(status.cycle.ends_at).toLocaleDateString()
  return `${start} - ${end}`
}

function TierProgress({ status }: { status: TierStatus }) {
  const usage = Number(status.authoritative_usage_kwh ?? 0)
  const finiteEnd = status.tiers.reduce(
    (maximum, tier) => Math.max(maximum, Number(tier.upper_bound_kwh ?? 0)),
    0,
  )
  const chartEnd = Math.max(usage, finiteEnd, 1)
  return <div className="tier-progress" aria-label={`Billing-cycle usage ${number(status.authoritative_usage_kwh)} kilowatt-hours`}>
    <div className="tier-progress-track">
      {status.tiers.map((tier, index) => {
        const lower = Number(tier.lower_bound_kwh)
        const upper = tier.upper_bound_kwh ? Number(tier.upper_bound_kwh) : chartEnd
        const width = Math.max(0, Math.min(chartEnd, upper) - Math.min(chartEnd, lower)) / chartEnd * 100
        const consumed = Math.max(0, Math.min(usage, upper) - lower)
        const available = Math.max(upper - lower, 0)
        return <span
          className={`tier-progress-segment tier-color-${index % 5}`}
          key={tier.tier_id}
          style={{ width: `${width}%`, '--tier-fill': `${available ? Math.min(100, consumed / available * 100) : 100}%` } as React.CSSProperties}
          title={`${tier.name}: ${number(tier.usage_kwh)} kWh`}
        />
      })}
    </div>
    <div className="tier-progress-labels">{status.tiers.map((tier) => <span key={tier.tier_id}><strong>{tier.name}</strong><small>{tier.upper_bound_kwh ? `to ${number(tier.upper_bound_kwh)} kWh` : `${number(tier.lower_bound_kwh)}+ kWh`}</small></span>)}</div>
  </div>
}

export function UsagePage() {
  const siteId = useSelectedSiteId()
  const accounts = useQuery({
    queryKey: ['utility-accounts', 'usage', siteId],
    queryFn: () => api<UtilityAccount[]>('/api/v1/utility-accounts'),
  })
  const scopedAccounts = (accounts.data ?? []).filter((account) => !siteId || account.site_id === siteId)
  const account = scopedAccounts.find((item) => item.status === 'active') ?? scopedAccounts[0]
  const status = useQuery({
    queryKey: ['tier-status', account?.id],
    queryFn: () => api<TierStatus>(`/api/v1/utility-accounts/${account?.id}/tier-status`),
    enabled: Boolean(account),
    refetchInterval: 60_000,
  })

  if (accounts.isLoading) return <LoadingState label="Loading utility-account usage..." />
  if (accounts.error) return <ErrorState error={accounts.error} retry={() => void accounts.refetch()} />
  return <>
    <PageTitle
      eyebrow="Billing-cycle allocation"
      title="Usage"
      description="Account-authoritative energy is allocated chronologically through the exact billing cycle and its configured tiers."
    />
    {!account ? <Panel><EmptyState title="No utility account" message="Create a utility account, assign a rate, and select an account-usage authority before tier progress can be shown." action={<Link className="button primary" to="/admin?tab=sites-accounts">Configure utility account <ArrowUpRight size={15} /></Link>} /></Panel>
      : status.isLoading ? <LoadingState label="Allocating billing-cycle usage..." />
        : status.error ? <ErrorState error={status.error} retry={() => void status.refetch()} />
          : !status.data?.available ? <Panel><EmptyState title="Tier usage is not available" message={status.data?.warnings[0] ?? 'The current account does not have a tiered rate and complete-account usage authority.'} action={<Link className="button primary" to="/admin?tab=sites-accounts">Complete account setup <ArrowUpRight size={15} /></Link>} /></Panel>
            : <UsageContent status={status.data} />}
  </>
}

function UsageContent({ status }: { status: TierStatus }) {
  return <>
    {status.warnings.map((warning) => <p className="billing-warning" key={warning}><AlertTriangle size={16} /><span>{warning}</span></p>)}
    <section className="billing-hero">
      <article><CalendarDays /><span><small>Billing cycle</small><strong>{cycleLabel(status)}</strong><em>{status.cycle.days_remaining} days remaining</em></span></article>
      <article><Gauge /><span><small>Usage to date</small><strong>{number(status.authoritative_usage_kwh)} kWh</strong><em>{number(status.coverage_percent, 0)}% interval coverage</em></span></article>
      <article><Layers3 /><span><small>Current tier</small><strong>{status.current_tier?.name ?? 'Unavailable'}</strong><em>{status.remaining_kwh ? `${number(status.remaining_kwh)} kWh until next tier` : 'Highest configured tier'}</em></span></article>
    </section>
    <Panel title="Usage by tier" eyebrow="Chronological allocation" actions={<StatusPill status={status.projection_confidence ?? 'info'} label={`${status.projection_confidence ?? 'unknown'} projection confidence`} />}>
      <TierProgress status={status} />
      <div className="responsive-table"><table><thead><tr><th>Tier</th><th>Tier range</th><th>Usage</th><th>Energy charge</th></tr></thead><tbody>
        {status.tiers.map((tier) => <tr key={tier.tier_id}><td><strong>{tier.name}</strong></td><td>{number(tier.lower_bound_kwh)} - {tier.upper_bound_kwh ? `${number(tier.upper_bound_kwh)} kWh` : 'No upper limit'}</td><td>{number(tier.usage_kwh, 3)} kWh</td><td>{tier.energy_charge ? `$${number(tier.energy_charge, 2)}` : 'Unavailable'}</td></tr>)}
      </tbody></table></div>
    </Panel>
    <div className="billing-summary-grid">
      <Panel title={`${number(status.authoritative_usage_kwh)} kWh`} eyebrow="Actual usage"><p className="panel-copy">Authority: {status.usage_authority.authority_type?.replaceAll('_', ' ') ?? 'not configured'} ({status.usage_authority.confidence}).</p></Panel>
      <Panel title={`${number(status.projected_usage_kwh)} kWh`} eyebrow="Projected cycle usage"><p className="panel-copy">{status.projection_method?.replaceAll('_', ' ')} projection; projected final tier: {status.projected_final_tier?.name ?? 'unavailable'}.</p></Panel>
      <Panel title={status.current_tier?.threshold_basis === 'daily_baseline_kwh' ? `${number(status.current_tier.derived_baseline_kwh)} kWh` : 'Fixed thresholds'} eyebrow="Threshold source"><p className="panel-copy">{status.current_tier?.threshold_basis.replaceAll('_', ' ') ?? 'Unavailable'} with {status.current_tier?.rounding_policy.replaceAll('_', ' ') ?? 'no'} rounding.</p></Panel>
      <Panel title={status.current_energy_price ? `$${number(status.current_energy_price, 5)}/kWh` : 'Unavailable'} eyebrow="Current energy context"><p className="panel-copy">{status.current_rate_period ?? status.current_tier?.name ?? 'No active tier'}; blended cycle energy rate {status.blended_energy_rate ? `$${number(status.blended_energy_rate, 5)}/kWh` : 'unavailable'}.</p></Panel>
    </div>
    <p className="billing-disclosure">{status.disclosure}</p>
  </>
}
