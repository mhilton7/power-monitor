import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Copy, Radio, ShieldCheck, X } from 'lucide-react'
import { useState } from 'react'
import { json, request } from '../../api/client'
import { adaptSensors } from '../../api/adapters'
import type { EnrollmentCode, Home } from '../../types/models'
import { InlineNotice } from '../../components/feedback/States'

interface Props {
  home: Home
  onClose: () => void
  onComplete?: () => void
}

export function SensorSetupFlow({ home, onClose, onComplete }: Props) {
  const client = useQueryClient()
  const [step, setStep] = useState(0)
  const [name, setName] = useState('Main panel')
  const [role, setRole] = useState('main')
  const [ctRating, setCtRating] = useState('100')
  const [connection, setConnection] = useState('push')
  const [enrollment, setEnrollment] = useState<EnrollmentCode>()
  const [copied, setCopied] = useState(false)
  const claimedSensors = useQuery({
    queryKey: ['sensors', home.id, 'enrollment-wait'],
    queryFn: () => request(`/api/v1/devices?site_id=${encodeURIComponent(home.id)}`, {}, adaptSensors),
    enabled: step >= 4,
    refetchInterval: step === 4 ? 3_000 : false,
  })
  const claimed = claimedSensors.data?.find((sensor) => sensor.name === name && sensor.lastSeenAt)
  const create = useMutation({
    mutationFn: () => request<Record<string, unknown>>('/api/v1/enrollment-tokens', json('POST', {
      expires_in_seconds: 600,
      site_id: home.id,
      name,
      measurement_role: role,
      ct_rating_amps: ctRating,
      connection_mode: connection,
    })),
    onSuccess: (value) => {
      setEnrollment({
        id: String(value.id),
        code: String(value.token),
        expiresAt: String(value.expires_at),
        name,
      })
      setStep(3)
      void client.invalidateQueries({ queryKey: ['sensors', home.id] })
    },
  })
  const steps = ['Name', 'Measurement', 'Connection', 'Connect', 'Verify', 'Done']

  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal-card setup-modal" role="dialog" aria-modal="true" aria-labelledby="sensor-setup-title">
        <header>
          <div><small>Sensor setup · Step {step + 1} of {steps.length}</small><h2 id="sensor-setup-title">{steps[step]}</h2></div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close sensor setup"><X /></button>
        </header>
        <ol className="stepper" aria-label="Sensor setup progress">
          {steps.map((label, index) => <li key={label} className={index <= step ? 'active' : ''}><span>{index < step ? <Check /> : index + 1}</span><small>{label}</small></li>)}
        </ol>

        <div className="setup-body">
          {step === 0 && <label>Sensor name<input value={name} onChange={(event) => { setName(event.target.value); }} autoFocus /></label>}
          {step === 1 && (
            <fieldset className="choice-grid"><legend>What does this sensor measure?</legend>
              {([
                ['main', 'Whole-home main', 'Use for the primary service measurement.'],
                ['service-leg', 'Service leg', 'One leg of a split-phase service.'],
                ['branch', 'Circuit or appliance', 'A partial load that must not be double counted.'],
                ['submeter', 'Submeter', 'A downstream energy-only measurement.'],
              ] as Array<[string, string, string]>).map(([value, label, detail]) => (
                <label className="choice-card" key={value}><input type="radio" name="role" value={value} checked={role === value} onChange={() => { setRole(value); }} /><span><strong>{label}</strong><small>{detail}</small></span></label>
              ))}
              <label>CT rating (amps)<input type="number" min="1" max="5000" value={ctRating} onChange={(event) => { setCtRating(event.target.value); }} /></label>
            </fieldset>
          )}
          {step === 2 && (
            <fieldset className="choice-grid"><legend>How should it connect?</legend>
              <label className="choice-card"><input type="radio" name="connection" checked={connection === 'push'} onChange={() => { setConnection('push'); }} /><span><strong>Sensor sends readings</strong><small>Recommended. Signed heartbeats provide its current address.</small></span></label>
              <label className="choice-card"><input type="radio" name="connection" checked={connection === 'hybrid'} onChange={() => { setConnection('hybrid'); }} /><span><strong>Send and recover history</strong><small>Push live readings and allow API/TCP backfill.</small></span></label>
              <label className="choice-card"><input type="radio" name="connection" checked={connection === 'pull'} onChange={() => { setConnection('pull'); }} /><span><strong>Server fetches readings</strong><small>Requires an explicit server-to-device network policy.</small></span></label>
              <InlineNotice><ShieldCheck /> Credentials stay on the server and device. They are never exposed to this browser.</InlineNotice>
            </fieldset>
          )}
          {step === 3 && enrollment && (
            <div className="connect-code">
              <Radio aria-hidden="true" />
              <h3>Enter this one-time code on the sensor</h3>
              <code>{enrollment.code}</code>
              <button type="button" className="button secondary" onClick={() => {
                void navigator.clipboard.writeText(enrollment.code)
                setCopied(true)
              }}><Copy /> {copied ? 'Copied' : 'Copy code'}</button>
              <p>It expires at {new Date(enrollment.expiresAt).toLocaleTimeString()} and can only be claimed once.</p>
            </div>
          )}
          {step === 4 && (
            <div className="verification-list">
              <p>Power on the sensor after entering its code. This page verifies its signed heartbeat, clock, PZEM acquisition, and storage status.</p>
              {claimed
                ? <InlineNotice tone="success"><Check /> Signed heartbeat received from {claimed.name}.</InlineNotice>
                : <InlineNotice>The server is waiting for the first valid signed heartbeat. This can take a few moments.</InlineNotice>}
            </div>
          )}
          {step === 5 && <div className="finish-state"><Check /><h3>Sensor setup prepared</h3><p>{name} will appear on Home as soon as it sends its first valid heartbeat.</p></div>}
          {create.error && <InlineNotice tone="danger">{create.error.message}</InlineNotice>}
        </div>

        <footer>
          <button type="button" className="button secondary" disabled={step === 0 || step === 3} onClick={() => { setStep((value) => Math.max(0, value - 1)); }}>Back</button>
          {step < 2 && <button type="button" className="button primary" disabled={step === 0 && !name.trim()} onClick={() => { setStep((value) => value + 1); }}>Continue</button>}
          {step === 2 && <button type="button" className="button primary" disabled={create.isPending} onClick={() => { create.mutate(); }}>{create.isPending ? 'Creating…' : 'Create secure code'}</button>}
          {step === 3 && <button type="button" className="button primary" onClick={() => { setStep(4); }}>I entered the code</button>}
          {step === 4 && <button type="button" className="button primary" disabled={!claimed} onClick={() => { void client.invalidateQueries({ queryKey: ['sensors', home.id] }); setStep(5); }}>Continue</button>}
          {step === 5 && <button type="button" className="button primary" onClick={() => { onComplete?.(); onClose() }}>Done</button>}
        </footer>
      </section>
    </div>
  )
}
