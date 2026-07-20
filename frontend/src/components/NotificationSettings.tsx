import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BellRing, CheckCircle2, Mail, Power, RefreshCw, Send, ServerCog, ShieldCheck, Unplug, X } from 'lucide-react'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { api, ApiError } from '../api'
import { ErrorState, LoadingState, Panel, StatusPill, formatTime } from './UI'

interface NotificationChannel {
  id: string
  name: string
  channel_type: 'smtp' | 'https_webhook' | 'in_app'
  enabled: boolean
  target: {
    host?: string
    port?: number
    from?: string
    recipient_count?: number
    starttls?: boolean
    implicit_tls?: boolean
    authentication_configured?: boolean
    event_types?: string[]
  }
  secrets_redacted: boolean
}

interface AlertRule {
  id: string
  name: string
  rule_type: string
  severity: string
  enabled: boolean
  site_id?: string
  device_id?: string
  debounce_seconds: number
  resolve_seconds: number
  configuration: Record<string, unknown>
}

interface NotificationAttempt {
  id: string
  attempted_at: string
  status: string
  response_summary?: string
  is_test: boolean
}

const splitRecipients = (value: string) => value
  .split(/[;,\n]/)
  .map((item) => item.trim())
  .filter(Boolean)

const rateEmailEvents = [
  'rate_source_changed',
  'rate_candidate_pending',
  'rate_candidate_validation_failed',
  'rate_source_unavailable',
  'rate_parser_failed',
  'rate_source_conflict',
  'rate_version_auto_activated',
  'rate_source_stale',
]

export function NotificationSettings() {
  const queryClient = useQueryClient()
  const channels = useQuery({ queryKey: ['notification-channels'], queryFn: () => api<NotificationChannel[]>('/api/v1/notification-channels') })
  const rules = useQuery({ queryKey: ['alert-rules'], queryFn: () => api<AlertRule[]>('/api/v1/alert-rules') })
  const attempts = useQuery({ queryKey: ['notification-attempts'], queryFn: () => api<NotificationAttempt[]>('/api/v1/notification-attempts?limit=10') })
  const smtp = useMemo(() => channels.data?.find((channel) => channel.channel_type === 'smtp'), [channels.data])

  const [showSmtpForm, setShowSmtpForm] = useState(false)
  const [channelName, setChannelName] = useState('Power Monitor email')
  const [host, setHost] = useState('')
  const [port, setPort] = useState(587)
  const [tlsMode, setTlsMode] = useState<'starttls' | 'implicit' | 'none'>('starttls')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [sender, setSender] = useState('')
  const [recipients, setRecipients] = useState('')
  const [emailDisconnects, setEmailDisconnects] = useState(true)
  const [emailSurges, setEmailSurges] = useState(true)
  const [emailRateUpdates, setEmailRateUpdates] = useState(true)
  const [smtpInitialized, setSmtpInitialized] = useState(false)

  const [disconnectEnabled, setDisconnectEnabled] = useState(true)
  const [disconnectDelay, setDisconnectDelay] = useState(60)
  const [surgeEnabled, setSurgeEnabled] = useState(false)
  const [surgeThreshold, setSurgeThreshold] = useState(5000)
  const [surgeDuration, setSurgeDuration] = useState(10)
  const [rulesInitialized, setRulesInitialized] = useState(false)

  useEffect(() => {
    if (!smtp || smtpInitialized) return
    setChannelName(smtp.name)
    setHost(smtp.target.host ?? '')
    setPort(smtp.target.port ?? 587)
    setSender(smtp.target.from ?? '')
    setTlsMode(smtp.target.implicit_tls ? 'implicit' : smtp.target.starttls === false ? 'none' : 'starttls')
    const selected = smtp.target.event_types ?? []
    setEmailDisconnects(!selected.length || selected.includes('heartbeat_stale'))
    setEmailSurges(!selected.length || selected.includes('power_surge'))
    setEmailRateUpdates(!selected.length || rateEmailEvents.some((event) => selected.includes(event)))
    setSmtpInitialized(true)
  }, [smtp, smtpInitialized])

  useEffect(() => {
    if (!rules.data || rulesInitialized) return
    const disconnect = rules.data.find((rule) => rule.rule_type === 'heartbeat_stale' && !rule.site_id && !rule.device_id)
    const surge = rules.data.find((rule) => rule.rule_type === 'power_surge' && !rule.site_id && !rule.device_id)
    if (disconnect) {
      setDisconnectEnabled(disconnect.enabled)
      setDisconnectDelay(Number(disconnect.configuration.stale_seconds ?? 60))
    }
    if (surge) {
      setSurgeEnabled(surge.enabled)
      setSurgeThreshold(Number(surge.configuration.threshold_watts ?? 5000))
      setSurgeDuration(surge.debounce_seconds)
    }
    setRulesInitialized(true)
  }, [rules.data, rulesInitialized])

  const saveSmtp = useMutation({
    mutationFn: () => {
      const eventTypes = [
        emailDisconnects && 'heartbeat_stale',
        emailSurges && 'power_surge',
        ...(emailRateUpdates ? rateEmailEvents : []),
      ].filter(Boolean)
      const configuration: Record<string, unknown> = {
        host: host.trim(),
        port,
        from: sender.trim(),
        recipients: splitRecipients(recipients),
        starttls: tlsMode === 'starttls',
        implicit_tls: tlsMode === 'implicit',
        event_types: eventTypes,
      }
      if (username.trim()) configuration.username = username.trim()
      if (password) configuration.password = password
      return api<NotificationChannel>(smtp ? `/api/v1/notification-channels/${smtp.id}` : '/api/v1/notification-channels', {
        method: smtp ? 'PUT' : 'POST',
        body: JSON.stringify({ name: channelName.trim(), channel_type: 'smtp', enabled: true, configuration }),
      })
    },
    onSuccess: async () => {
      setPassword('')
      setRecipients('')
      setShowSmtpForm(false)
      setSmtpInitialized(false)
      await queryClient.invalidateQueries({ queryKey: ['notification-channels'] })
    },
  })

  const testSmtp = useMutation({
    mutationFn: () => api<{ attempt_id: string }>(`/api/v1/notification-channels/${smtp?.id}/test`, { method: 'POST' }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ['notification-attempts'] }),
  })

  const disableSmtp = useMutation({
    mutationFn: () => api<void>(`/api/v1/notification-channels/${smtp?.id}`, { method: 'DELETE' }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ['notification-channels'] }),
  })

  const saveRules = useMutation({
    mutationFn: async () => {
      const disconnect = rules.data?.find((rule) => rule.rule_type === 'heartbeat_stale' && !rule.site_id && !rule.device_id)
      const surge = rules.data?.find((rule) => rule.rule_type === 'power_surge' && !rule.site_id && !rule.device_id)
      const upsert = (existing: AlertRule | undefined, payload: Record<string, unknown>) => api(
        existing ? `/api/v1/alert-rules/${existing.id}` : '/api/v1/alert-rules',
        { method: existing ? 'PUT' : 'POST', body: JSON.stringify(payload) },
      )
      await Promise.all([
        upsert(disconnect, {
          name: 'Sensor disconnected', rule_type: 'heartbeat_stale', severity: 'critical', enabled: disconnectEnabled,
          debounce_seconds: 0, resolve_seconds: 30, configuration: { stale_seconds: disconnectDelay },
        }),
        upsert(surge, {
          name: 'Power surge', rule_type: 'power_surge', severity: 'critical', enabled: surgeEnabled,
          debounce_seconds: surgeDuration, resolve_seconds: 30, configuration: { threshold_watts: surgeThreshold },
        }),
      ])
    },
    onSuccess: async () => {
      setRulesInitialized(false)
      await queryClient.invalidateQueries({ queryKey: ['alert-rules'] })
    },
  })

  const smtpProblem = saveSmtp.error instanceof ApiError ? saveSmtp.error.problem : undefined
  const ruleProblem = saveRules.error instanceof ApiError ? saveRules.error.problem : undefined
  const selectedEmailEvents = Number(emailDisconnects) + Number(emailSurges) + Number(emailRateUpdates)

  if (channels.isLoading || rules.isLoading) return <LoadingState label="Loading notification settings…" />
  if (channels.error) return <ErrorState error={channels.error} retry={() => { void channels.refetch() }} />
  if (rules.error) return <ErrorState error={rules.error} retry={() => { void rules.refetch() }} />

  return (
    <div className="notification-stack">
      <Panel
        title="SMTP email service"
        eyebrow="Credentials encrypted at rest"
        actions={<button className="button primary" onClick={() => { setShowSmtpForm((value) => !value); saveSmtp.reset() }}><ServerCog size={16} /> {smtp ? 'Edit SMTP' : 'Configure SMTP'}</button>}
      >
        {smtp ? (
          <div className="smtp-summary">
            <span><Mail /></span>
            <div><strong>{smtp.name}</strong><small>{smtp.target.host}:{smtp.target.port} · {smtp.target.recipient_count} recipient{smtp.target.recipient_count === 1 ? '' : 's'} · {smtp.target.implicit_tls ? 'TLS' : smtp.target.starttls ? 'STARTTLS' : 'No transport encryption'}</small></div>
            <StatusPill status={smtp.enabled ? 'healthy' : 'failed'} label={smtp.enabled ? 'Enabled' : 'Disabled'} />
            <button className="button secondary" disabled={!smtp.enabled || testSmtp.isPending} onClick={() => { testSmtp.mutate() }}><Send size={15} /> {testSmtp.isPending ? 'Queued…' : 'Send test'}</button>
            <button className="icon-button" aria-label="Disable SMTP" disabled={!smtp.enabled || disableSmtp.isPending} onClick={() => { disableSmtp.mutate() }}><X /></button>
          </div>
        ) : <div className="notification-empty"><Mail /><div><strong>No SMTP service configured</strong><p>Add a trusted mail relay to receive selected device alerts. Passwords are never returned to the browser after saving.</p></div></div>}

        {showSmtpForm && (
          <form className="settings-form" onSubmit={(event: FormEvent) => { event.preventDefault(); saveSmtp.mutate() }}>
            {smtpProblem && <div className="form-error" role="alert"><strong>{smtpProblem.title}</strong><span>{smtpProblem.detail}</span></div>}
            <div className="form-columns">
              <label><span>Configuration name</span><input value={channelName} onChange={(event) => { setChannelName(event.target.value) }} required /></label>
              <label><span>SMTP host</span><input value={host} onChange={(event) => { setHost(event.target.value) }} placeholder="smtp.example.com" required /></label>
              <label><span>Port</span><input type="number" min="1" max="65535" value={port} onChange={(event) => { setPort(Number(event.target.value)) }} required /></label>
              <label><span>Transport security</span><select value={tlsMode} onChange={(event) => { setTlsMode(event.target.value as typeof tlsMode) }}><option value="starttls">STARTTLS (recommended)</option><option value="implicit">Implicit TLS</option><option value="none">None (unauthenticated relay only)</option></select></label>
              <label><span>Username</span><input value={username} onChange={(event) => { setUsername(event.target.value) }} autoComplete="username" placeholder={smtp?.target.authentication_configured ? 'Leave blank to retain saved login' : 'Optional'} /></label>
              <label><span>Password</span><input type="password" value={password} onChange={(event) => { setPassword(event.target.value) }} autoComplete="new-password" placeholder={smtp?.target.authentication_configured ? 'Leave blank to retain saved password' : 'Optional'} /></label>
              <label><span>From address</span><input type="email" value={sender} onChange={(event) => { setSender(event.target.value) }} placeholder="power-monitor@example.com" required /></label>
              <label><span>Recipients</span><textarea value={recipients} onChange={(event) => { setRecipients(event.target.value) }} placeholder={smtp ? 'Re-enter recipients when changing SMTP settings' : 'alerts@example.com, owner@example.com'} rows={2} required /></label>
            </div>
            <fieldset className="notification-event-options">
              <legend>Email these events</legend>
              <label><input type="checkbox" checked={emailDisconnects} onChange={(event) => { setEmailDisconnects(event.target.checked) }} /><span><Unplug /><strong>Sensor disconnects</strong><small>After the configured heartbeat delay</small></span></label>
              <label><input type="checkbox" checked={emailSurges} onChange={(event) => { setEmailSurges(event.target.checked) }} /><span><Power /><strong>Power surges</strong><small>After the threshold persists</small></span></label>
              <label><input aria-label="Email SCE rate updates" type="checkbox" checked={emailRateUpdates} onChange={(event) => { setEmailRateUpdates(event.target.checked) }} /><span><RefreshCw /><strong>SCE rate updates</strong><small>Source failures, candidates, conflicts, and safe activation</small></span></label>
            </fieldset>
            {!selectedEmailEvents && <p className="field-error">Select at least one event to email.</p>}
            <footer><button type="button" className="button secondary" onClick={() => { setShowSmtpForm(false) }}>Cancel</button><button className="button primary" disabled={saveSmtp.isPending || !selectedEmailEvents}><ShieldCheck size={16} /> {saveSmtp.isPending ? 'Saving…' : 'Save SMTP securely'}</button></footer>
          </form>
        )}
      </Panel>

      <Panel title="Notification triggers" eyebrow="Choose what happens and when">
        {ruleProblem && <div className="form-error" role="alert"><strong>{ruleProblem.title}</strong><span>{ruleProblem.detail}</span></div>}
        <form className="trigger-grid" onSubmit={(event) => { event.preventDefault(); saveRules.mutate() }}>
          <article className="trigger-card">
            <header><span><Unplug /></span><div><strong>Sensor disconnected</strong><small>Uses signed heartbeat arrival, never ICMP or mDNS</small></div><label className="switch"><input aria-label="Enable sensor disconnect notifications" type="checkbox" checked={disconnectEnabled} onChange={(event) => { setDisconnectEnabled(event.target.checked) }} /><span /></label></header>
            <label><span>Notify after no heartbeat for</span><div className="input-unit"><input aria-label="Sensor disconnect delay" type="number" min="15" max="86400" value={disconnectDelay} onChange={(event) => { setDisconnectDelay(Number(event.target.value)) }} /><span>seconds</span></div></label>
          </article>
          <article className="trigger-card">
            <header><span><Power /></span><div><strong>Power surge</strong><small>Uses the latest signed device power measurement</small></div><label className="switch"><input aria-label="Enable power surge notifications" type="checkbox" checked={surgeEnabled} onChange={(event) => { setSurgeEnabled(event.target.checked) }} /><span /></label></header>
            <div className="form-columns"><label><span>Power threshold</span><div className="input-unit"><input aria-label="Power surge threshold" type="number" min="1" max="10000000" value={surgeThreshold} onChange={(event) => { setSurgeThreshold(Number(event.target.value)) }} /><span>W</span></div></label><label><span>Must persist for</span><div className="input-unit"><input aria-label="Power surge duration" type="number" min="0" max="86400" value={surgeDuration} onChange={(event) => { setSurgeDuration(Number(event.target.value)) }} /><span>seconds</span></div></label></div>
          </article>
          <footer><p><CheckCircle2 /> Changes apply to every enrolled sensor unless a scoped rule is added through the API.</p><button className="button primary" disabled={saveRules.isPending}><BellRing size={16} /> {saveRules.isPending ? 'Saving…' : 'Save notification triggers'}</button></footer>
        </form>
      </Panel>

      <Panel title="Recent delivery attempts" eyebrow="Retry and test evidence">
        {attempts.isLoading ? <LoadingState /> : attempts.error ? <ErrorState error={attempts.error} /> : attempts.data?.length ? <div className="delivery-list">{attempts.data.map((attempt) => <article key={attempt.id}><span><Send /></span><div><strong>{attempt.is_test ? 'SMTP test' : 'Alert notification'}</strong><small>{formatTime(attempt.attempted_at)} · {attempt.response_summary ?? 'Waiting for worker delivery'}</small></div><StatusPill status={attempt.status} /></article>)}</div> : <div className="notification-empty"><Send /><div><strong>No delivery attempts yet</strong><p>Save SMTP, then send a test message before relying on operational alerts.</p></div></div>}
      </Panel>
    </div>
  )
}
