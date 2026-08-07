import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, FileText, Home, Radio, ShieldCheck, Sparkles, Zap } from 'lucide-react'
import { useEffect, useState } from 'react'
import { json, request } from '../../api/client'
import { InlineNotice } from '../../components/feedback/States'
import { BillImportFlow } from '../../features/bill-import/BillImportFlow'
import { SensorSetupFlow } from '../../features/sensors/SensorSetupFlow'
import { useLiveHome } from '../../state/LiveHomeContext'
import { useSingleHome } from '../../state/SingleHomeContext'
import { useAuth } from '../../state/AuthContext'
import type { Home as HomeModel } from '../../types/models'

const STEPS = [
  'Create Owner',
  'Name your home',
  'Time and currency',
  'Select utility',
  'Upload bill',
  'Review rate',
  'Connect a sensor',
  'Verify reading',
  'Finish',
]

export function OnboardingPage() {
  const client = useQueryClient()
  const { resolution, refresh } = useSingleHome()
  const { services, sensors } = useLiveHome()
  const { session } = useAuth()
  const persisted = Number(localStorage.getItem('pm-single-home-onboarding-step') ?? '0')
  const [step, setStep] = useState(Number.isFinite(persisted) ? Math.min(Math.max(persisted, 0), 8) : 0)
  const existing = resolution?.state === 'ready' ? resolution.home : undefined
  const [name, setName] = useState(existing?.name ?? 'My Home')
  const [timezone, setTimezone] = useState(existing?.timezone ?? (Intl.DateTimeFormat().resolvedOptions().timeZone || 'America/Los_Angeles'))
  const [currency, setCurrency] = useState(existing?.currency ?? 'USD')
  const [serviceName, setServiceName] = useState('Home electric service')
  const [billingDay, setBillingDay] = useState('1')
  const [billOpen, setBillOpen] = useState(false)
  const [sensorOpen, setSensorOpen] = useState(false)
  const home = existing
  useEffect(() => { localStorage.setItem('pm-single-home-onboarding-step', String(step)); }, [step])

  const saveHome = useMutation({
    mutationFn: async () => {
      if (existing) {
        return request<HomeModel>(`/api/v1/admin/sites/${existing.id}`, json('PUT', {
          revision: existing.revision,
          name,
          timezone,
          currency,
          timezone_change_confirmed: timezone !== existing.timezone,
          reason: 'Single Home onboarding',
        }))
      }
      return request<HomeModel>('/api/v1/sites', json('POST', {
        name,
        code: name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 80) || 'home',
        timezone,
        currency,
        locale: navigator.language || 'en-US',
        unit_system: 'imperial',
        allowed_cidrs: [],
        allowed_domains: [],
        allow_public_polling: false,
      }))
    },
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['single-home'] })
      await refresh()
      setStep(3)
    },
  })
  const createService = useMutation({
    mutationFn: () => request('/api/v1/utility-accounts', json('POST', {
      site_id: home?.id,
      name: serviceName,
      timezone,
      currency,
      billing_cycle_start_day: Number(billingDay),
      generation_provider: 'sce',
    })),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['electric-services'] })
      setStep(4)
    },
  })

  const continueTo = (value: number) => { setStep(Math.min(8, Math.max(0, value))); }
  const finish = () => {
    localStorage.setItem('pm-single-home-onboarding-complete', 'true')
    localStorage.removeItem('pm-single-home-onboarding-step')
    window.location.assign('/home')
  }

  return (
    <main className="onboarding-shell">
      <section className="onboarding-card" aria-labelledby="onboarding-title">
        <aside>
          <div className="brand"><span className="brand-mark"><Zap fill="currentColor" /></span><div><strong>Power Monitor</strong><small>Single Home setup</small></div></div>
          <ol>{STEPS.map((label, index) => <li key={label} className={index === step ? 'active' : index < step ? 'complete' : ''}><span>{index < step ? <Check /> : index + 1}</span>{label}</li>)}</ol>
        </aside>
        <div className="onboarding-content">
          <header><small>Step {step + 1} of 9</small><h1 id="onboarding-title">{STEPS[step]}</h1></header>
          {step === 0 && <div className="welcome-panel"><Sparkles /><h2>{session?.user?.name ?? 'Home owner'} is ready.</h2><p>The authenticated Owner account controls household setup, family access, billing, and backups. A fresh deployment creates this account securely before opening onboarding.</p><InlineNotice><ShieldCheck /> Existing users, readings, rate plans, bills, alerts, and audit history are preserved.</InlineNotice></div>}
          {step === 1 && <div className="setup-body"><Home className="hero-icon" /><label>What should we call this home?<input value={name} autoFocus onChange={(event) => { setName(event.target.value); }} /></label></div>}
          {step === 2 && <div className="setup-body form-grid"><label>Timezone<input value={timezone} onChange={(event) => { setTimezone(event.target.value); }} /></label><label>Currency<select value={currency} onChange={(event) => { setCurrency(event.target.value); }}><option>USD</option><option>CAD</option><option>EUR</option><option>GBP</option></select></label><p className="span-all">Tariffs are evaluated in this timezone. Readings remain stored in UTC.</p></div>}
          {step === 3 && <div className="setup-body form-grid"><label>Service name<input value={serviceName} onChange={(event) => { setServiceName(event.target.value); }} /></label><label>Billing cycle starts on<input type="number" min="1" max="31" value={billingDay} onChange={(event) => { setBillingDay(event.target.value); }} /></label><InlineNotice>Southern California Edison is the installed utility source. You can configure another provider later in Billing.</InlineNotice></div>}
          {step === 4 && <div className="choice-intro"><FileText /><h2>Import an electric bill</h2><p>A bill can prepare a separate rate-plan draft and billing-cycle draft. You review everything before applying it.</p>{services.length > 0 && <button type="button" className="button primary" onClick={() => { setBillOpen(true); }}>Upload electric bill</button>}<button type="button" className="text-button" onClick={() => { continueTo(5); }}>Skip for now</button></div>}
          {step === 5 && <div className="verification-list"><h2>Review the rate setup</h2><div className={services[0]?.currentPlan ? '' : 'warning'}><Check /><span><strong>{services[0]?.currentPlan ?? 'Rate review skipped'}</strong><small>{services[0]?.currentPlan ? 'The reviewed version is assigned to this electric service.' : 'Upload a bill later from Billing to prepare a reviewed plan.'}</small></span></div><InlineNotice>No bill can activate a plan until an administrator reviews and confirms its extracted values.</InlineNotice></div>}
          {step === 6 && <div className="choice-intro"><Radio /><h2>Connect your first sensor</h2><p>Create a short-lived, single-use code for an ESP32 power sensor.</p>{home && <button type="button" className="button primary" onClick={() => { setSensorOpen(true); }}>Connect sensor</button>}<button type="button" className="text-button" onClick={() => { continueTo(7); }}>Skip for now</button></div>}
          {step === 7 && <div className="verification-list"><h2>Verify the first signed reading</h2><div><Check /><span><strong>{home ? home.name : name}</strong><small>{timezone} · {currency}</small></span></div><div className={services.length ? '' : 'warning'}><Check /><span><strong>{services.length ? 'Electric service ready' : 'Electric service skipped'}</strong><small>{services[0]?.name ?? 'Add one later in Billing'}</small></span></div><div className={sensors.some((sensor) => sensor.lastSeenAt) ? '' : 'warning'}><Check /><span><strong>{sensors.some((sensor) => sensor.lastSeenAt) ? 'Signed reading received' : sensors.length ? 'Waiting for a signed reading' : 'Sensor skipped'}</strong><small>{sensors.find((sensor) => sensor.lastSeenAt)?.name ?? (sensors.length ? 'Keep this page open while the sensor connects.' : 'Add one later in Settings.')}</small></span></div></div>}
          {step === 8 && <div className="finish-state"><Check /><h2>Your home is ready</h2><p>Home will show live power when the first signed reading arrives. Billing calculations appear when an electric service and rate are active.</p></div>}
          {(saveHome.error || createService.error) && <InlineNotice tone="danger">{saveHome.error?.message ?? createService.error?.message}</InlineNotice>}
          <footer>
            {step > 0 && step < 8 && <button type="button" className="button secondary" onClick={() => { continueTo(step - 1); }}>Back</button>}
            {step === 0 && <button type="button" className="button primary" onClick={() => { continueTo(1); }}>Get started</button>}
            {step === 1 && <button type="button" className="button primary" disabled={!name.trim()} onClick={() => { continueTo(2); }}>Continue</button>}
            {step === 2 && <button type="button" className="button primary" disabled={saveHome.isPending} onClick={() => { saveHome.mutate(); }}>{saveHome.isPending ? 'Saving…' : 'Save home'}</button>}
            {step === 3 && (services.length ? <button type="button" className="button primary" onClick={() => { continueTo(4); }}>Continue</button> : <button type="button" className="button primary" disabled={createService.isPending || !home} onClick={() => { createService.mutate(); }}>{createService.isPending ? 'Creating…' : 'Create electric service'}</button>)}
            {step === 5 && <button type="button" className="button primary" onClick={() => { continueTo(6); }}>Continue</button>}
            {step === 7 && <button type="button" className="button primary" onClick={() => { continueTo(8); }}>Finish setup</button>}
            {step === 8 && <button type="button" className="button primary" onClick={finish}>Go to Home</button>}
          </footer>
        </div>
      </section>
      {home && billOpen && <BillImportFlow home={home} services={services} onClose={() => { setBillOpen(false); continueTo(5) }} />}
      {home && sensorOpen && <SensorSetupFlow home={home} onClose={() => { setSensorOpen(false); continueTo(7) }} />}
    </main>
  )
}
