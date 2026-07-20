import { useMutation } from '@tanstack/react-query'
import { Calculator, CircleDollarSign, Info, TrendingUp } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Bar } from 'react-chartjs-2'
import { BarElement, CategoryScale, Chart as ChartJS, Legend, LinearScale, Tooltip } from 'chart.js'
import { api } from '../api'
import { Disclosure, formatMoney, formatNumber, Metric, PageTitle, Panel } from '../components/UI'

ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip, Legend)
interface Preview { plan_code: string; rate_version: string; timezone: string; energy_by_bucket_kwh: Record<string, string>; components: Array<{ name: string; amount: string }>; unrounded_total: string; display_total: string; coverage_percent: string; disclosure: string }

export function CostsPage() {
  const [plan, setPlan] = useState('TOU-D-4-9PM')
  const [energy, setEnergy] = useState('20')
  const [scope, setScope] = useState('energy_only')
  const preview = useMutation({ mutationFn: () => api<Preview>('/api/v1/rates/preview', { method: 'POST', body: JSON.stringify({ plan_code: plan, interval_start: '2026-07-20T07:00:00Z', interval_end: '2026-07-21T07:00:00Z', energy_kwh: energy, cost_scope: scope, baseline_allocation_kwh: scope === 'full_account' ? '10' : null, billing_days: 1, cca_adjustment_per_kwh: '0', other_adjustment: '0' }) }) })
  const submit = (event: FormEvent) => { event.preventDefault(); preview.mutate() }
  const result = preview.data
  return (
    <>
      <PageTitle eyebrow="Utility estimate" title="Costs & billing" description="Energy charges stay separate from account-level service charges, credits, provider adjustments, taxes, and manual bill items." />
      <section className="metric-grid metric-grid-4"><Metric label="Current period" value="—" detail="Assign an account rate" /><Metric label="Monitored energy" value={result ? formatNumber(energy) : '—'} unit="kWh" detail={scope.replace('_', ' ')} /><Metric label="Estimated energy cost" value={result ? formatMoney(result.components[0]?.amount) : '—'} detail="Unrounded internally" /><Metric label="Estimated total" value={result ? formatMoney(result.display_total) : '—'} detail={`${result?.coverage_percent ?? '0'}% coverage`} /></section>
      <div className="cost-grid"><Panel eyebrow="Interactive estimate" title="Test calculator"><form className="calculator" onSubmit={submit}><label><span>Rate plan</span><select value={plan} onChange={(event) => { setPlan(event.target.value); }}><option>TOU-D-4-9PM</option><option>TOU-D-5-8PM</option><option>TOU-D-PRIME</option></select></label><label><span>Energy</span><div className="input-unit"><input type="number" min="0.001" step="0.001" value={energy} onChange={(event) => { setEnergy(event.target.value); }} /><span>kWh</span></div></label><label><span>Cost scope</span><select value={scope} onChange={(event) => { setScope(event.target.value); }}><option value="energy_only">Monitored-load energy only</option><option value="allocated_account">Allocated account</option><option value="full_account">Full utility account</option></select></label><button className="button primary" disabled={preview.isPending}><Calculator size={17} /> Calculate</button></form>{scope === 'full_account' && <div className="scope-warning"><Info size={17} /><p><strong>Full-account mode is explicit.</strong> Fixed charges apply once and baseline credit is capped by the configured allocation.</p></div>}</Panel><Panel eyebrow="Cost components" title="What makes up the estimate">{result ? <div className="component-list">{result.components.map((component) => <div key={component.name}><span>{component.name}</span><strong>{formatMoney(component.amount)}</strong></div>)}<div className="component-total"><span>Estimated total</span><strong>{formatMoney(result.display_total)}</strong></div></div> : <div className="calculator-prompt"><CircleDollarSign /><p><strong>Run a calculation</strong><small>See every charge and credit separately.</small></p></div>}</Panel></div>
      {result && <Panel title="Energy by TOU bucket" eyebrow={`${result.plan_code} · ${result.rate_version}`}><div className="bucket-chart"><Bar data={{ labels: Object.keys(result.energy_by_bucket_kwh), datasets: [{ label: 'kWh', data: Object.values(result.energy_by_bucket_kwh).map(Number), backgroundColor: ['#5da7ff', '#31d9a3', '#ffc857', '#ff7a90'], borderRadius: 6 }] }} options={{ responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid: { display: false } }, y: { beginAtZero: true } } }} /></div><div className="projection-note"><TrendingUp size={18} /><p><strong>Projection methods are coverage-aware.</strong> Straight-line, recent seven-day average, and same-weekday profiles are labeled estimates, never bills.</p></div></Panel>}
      <Disclosure />
    </>
  )
}

