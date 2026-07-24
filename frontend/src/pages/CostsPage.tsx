import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowUpRight, CircleDollarSign, ReceiptText, TrendingUp } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { EmptyState, ErrorState, LoadingState, PageTitle, Panel, StatusPill } from '../components/UI'
import { useSelectedSiteId } from '../hooks/useSelectedSite'
import type { TierStatus, UtilityAccount } from '../types'

function money(value?: string, currency = 'USD') {
  if (value === undefined || !Number.isFinite(Number(value))) return 'Unavailable'
  return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(Number(value))
}

function kwh(value?: string) {
  if (value === undefined || !Number.isFinite(Number(value))) return 'Unavailable'
  return `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 3 })} kWh`
}

export function CostsPage() {
  const siteId = useSelectedSiteId()
  const accounts = useQuery({
    queryKey: ['utility-accounts', 'costs', siteId],
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

  if (accounts.isLoading) return <LoadingState label="Loading account costs..." />
  if (accounts.error) return <ErrorState error={accounts.error} retry={() => void accounts.refetch()} />
  return <>
    <PageTitle
      eyebrow="Estimated account charges"
      title="Costs"
      description="Energy charges remain separate from fixed charges, credits, provider adjustments, and reconciled utility totals."
    />
    {!account ? <Panel><EmptyState title="No utility account" message="Create an account and assign a rate before account costs can be estimated." action={<Link className="button primary" to="/admin?tab=sites-accounts">Configure utility account <ArrowUpRight size={15} /></Link>} /></Panel>
      : status.isLoading ? <LoadingState label="Calculating billing-cycle costs..." />
        : status.error ? <ErrorState error={status.error} retry={() => void status.refetch()} />
          : !status.data?.available ? <Panel><EmptyState title="Tier costs are not available" message={status.data?.warnings[0] ?? 'Complete rate and usage-authority setup to calculate costs.'} action={<Link className="button primary" to="/admin?tab=sites-accounts">Review account setup <ArrowUpRight size={15} /></Link>} /></Panel>
            : <CostsContent status={status.data} />}
  </>
}

function CostsContent({ status }: { status: TierStatus }) {
  const components = status.bill_components
  return <>
    {status.warnings.map((warning) => <p className="billing-warning" key={warning}><AlertTriangle size={16} /><span>{warning}</span></p>)}
    <section className="billing-hero">
      <article><CircleDollarSign /><span><small>Energy charge to date</small><strong>{money(status.energy_charge, status.currency)}</strong><em>{kwh(status.authoritative_usage_kwh)} allocated</em></span></article>
      <article><ReceiptText /><span><small>Estimated total to date</small><strong>{money(status.estimated_total_bill, status.currency)}</strong><em>{components?.scope === 'full_account_estimate' ? 'Complete-account scope' : 'Account charges excluded'}</em></span></article>
      <article><TrendingUp /><span><small>Projected cycle total</small><strong>{money(status.projected_total_bill ?? status.projected_energy_charge, status.currency)}</strong><em>{status.projection_confidence ?? 'unknown'} confidence</em></span></article>
    </section>
    <Panel title={`${new Date(status.cycle.starts_at).toLocaleDateString()} - ${new Date(status.cycle.ends_at).toLocaleDateString()}`} eyebrow="Billing-cycle context">
      <dl className="calculation-evidence">
        <div><dt>Days remaining</dt><dd>{status.cycle.days_remaining} of {status.cycle.days}</dd></div>
        <div><dt>Current tier</dt><dd>{status.current_rate_period ?? status.current_tier?.name ?? 'Unavailable'}</dd></div>
        <div><dt>Projected final tier</dt><dd>{status.projected_final_tier?.name ?? 'Unavailable'}</dd></div>
        <div><dt>Blended energy rate</dt><dd>{status.blended_energy_rate ? `${money(status.blended_energy_rate, status.currency)}/kWh` : 'Unavailable'}</dd></div>
      </dl>
    </Panel>
    <div className="billing-cost-grid">
      <Panel title="Energy charge by tier" eyebrow="Unrounded calculation">
        <div className="responsive-table"><table><thead><tr><th>Tier</th><th>Usage</th><th>Rate</th><th>Charge</th></tr></thead><tbody>
          {status.tiers.map((tier) => <tr key={tier.tier_id}><td>{tier.name}</td><td>{kwh(tier.usage_kwh)}</td><td>{money(tier.price_per_kwh, status.currency)}/kWh</td><td>{money(tier.energy_charge, status.currency)}</td></tr>)}
        </tbody><tfoot><tr><th colSpan={3}>Energy subtotal</th><td>{money(status.energy_charge, status.currency)}</td></tr></tfoot></table></div>
      </Panel>
      <Panel title="Bill components" eyebrow="Scope-aware estimate" actions={<StatusPill status={components?.scope === 'full_account_estimate' ? 'healthy' : 'info'} label={components?.scope.replaceAll('_', ' ') ?? 'energy only'} />}>
        <dl className="bill-components">
          <div><dt>Energy charge</dt><dd>{money(status.energy_charge, status.currency)}</dd></div>
          <div><dt>Fixed charges</dt><dd>{money(components?.fixed_charge, status.currency)}</dd></div>
          <div><dt>Credits</dt><dd>{money(components?.credits, status.currency)}</dd></div>
          <div><dt>Other adjustments</dt><dd>{money(components?.adjustments, status.currency)}</dd></div>
          <div className="bill-total"><dt>Estimated total</dt><dd>{money(status.estimated_total_bill, status.currency)}</dd></div>
        </dl>
        {components?.scope !== 'full_account_estimate' && <p className="scope-warning">Fixed charges, baseline credits, taxes, and account-only adjustments are intentionally excluded because this account is not configured as a complete-account cost authority.</p>}
      </Panel>
    </div>
    <Panel title="Calculation evidence" eyebrow="Reproducibility">
      <dl className="calculation-evidence"><div><dt>Rate version</dt><dd>v{status.rate_version} ({status.rate_version_id})</dd></div><div><dt>Pricing model</dt><dd>{status.pricing_model?.replaceAll('_', ' ')}</dd></div><div><dt>Recalculation</dt><dd>Version {status.recalculation_version ?? 0}</dd></div><div><dt>Authority</dt><dd>{status.usage_authority.authority_type?.replaceAll('_', ' ')} / {status.usage_authority.confidence}</dd></div></dl>
    </Panel>
    {status.utility_bill_comparison && <Panel title="Finalized utility bill comparison" eyebrow="Reconciliation">
      <dl className="bill-components">
        <div><dt>Utility bill total</dt><dd>{money(status.utility_bill_comparison.utility_total, status.currency)}</dd></div>
        <div><dt>Server estimate</dt><dd>{money(status.utility_bill_comparison.estimated_total, status.currency)}</dd></div>
        <div><dt>Difference</dt><dd>{money(status.utility_bill_comparison.difference, status.currency)}</dd></div>
        <div><dt>Documented reconciliation</dt><dd>{money(status.utility_bill_comparison.reconciliation_adjustments, status.currency)}</dd></div>
        <div className="bill-total"><dt>Unexplained difference</dt><dd>{money(status.utility_bill_comparison.unexplained_difference, status.currency)}</dd></div>
      </dl>
      {status.utility_bill_comparison.reference && <p className="panel-copy">Evidence: {status.utility_bill_comparison.reference}</p>}
    </Panel>}
    <p className="billing-disclosure"><strong>Estimate, not utility bill.</strong> {status.disclosure}</p>
  </>
}
