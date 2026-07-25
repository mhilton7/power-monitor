import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Bell,
  DatabaseBackup,
  Gauge,
  Home,
  FileText,
  KeyRound,
  Mail,
  MoreHorizontal,
  Palette,
  Plus,
  Radio,
  RefreshCw,
  Rows3,
  Shield,
  Trash2,
  UserPlus,
  Users,
  Wifi,
  Wrench,
} from 'lucide-react'
import { useState } from 'react'
import { useLocation, useNavigate } from '../../app/router'
import { hasPermission, isOwner } from '../../access/permissions'
import {
  adaptBackups,
  adaptFamily,
  adaptFamilyRoles,
  adaptHealth,
  adaptPermissions,
} from '../../api/adapters'
import { json, request } from '../../api/client'
import { EmptyState, ErrorState, InlineNotice, LoadingState } from '../../components/feedback/States'
import { Surface } from '../../components/data-display/Surface'
import { SensorSetupFlow } from '../../features/sensors/SensorSetupFlow'
import { useAppearance } from '../../state/AppearanceContext'
import { useAuth } from '../../state/AuthContext'
import { useLiveHome } from '../../state/LiveHomeContext'
import { useSingleHome } from '../../state/SingleHomeContext'
import type { FamilyMember, FamilyRoleOption, PermissionOption } from '../../types/models'
import { fileSize, relativeTime } from '../../utils/format'
import { AdvancedRateSettings } from '../../features/rates/AdvancedRateSettings'

type Section = 'home' | 'sensors' | 'family' | 'notifications' | 'appearance' | 'data' | 'advanced'

const SECTIONS = [
  ['home', Home, 'Home'],
  ['sensors', Radio, 'Sensors'],
  ['family', Users, 'Family Access'],
  ['notifications', Bell, 'Notifications'],
  ['appearance', Palette, 'Appearance'],
  ['data', DatabaseBackup, 'Data & Backups'],
  ['advanced', Wrench, 'Advanced'],
] as const

export function SettingsPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const routeSection = location.pathname.split('/')[2]
  const initialSection = SECTIONS.some(([key]) => key === routeSection) ? routeSection as Section : 'home'
  const [section, setSection] = useState<Section>(initialSection)
  const { session } = useAuth()
  const owner = session ? isOwner(session) : false
  const visible = owner ? SECTIONS : SECTIONS.filter(([key]) => !['data', 'advanced'].includes(key))
  return (
    <div className="workspace-page settings-page">
      <header className="page-heading">
        <div><small>Manage your home</small><h1>Settings</h1><p>Update sensors, access, notifications, appearance, and local data.</p></div>
      </header>
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="Settings sections">
          {visible.map(([key, Icon, label]) => <button type="button" key={key} className={section === key ? 'active' : ''} onClick={() => { setSection(key); navigate(`/settings/${key}`); }}><Icon />{label}</button>)}
        </nav>
        <div className="settings-detail">
          {section === 'home' && <HomeSettings />}
          {section === 'sensors' && <SensorSettings />}
          {section === 'family' && <FamilySettings />}
          {section === 'notifications' && <NotificationSettings />}
          {section === 'appearance' && <AppearanceSettings />}
          {section === 'data' && owner && <DataSettings />}
          {section === 'advanced' && owner && <AdvancedSettings />}
        </div>
      </div>
    </div>
  )
}

function HomeSettings() {
  const client = useQueryClient()
  const { resolution, refresh } = useSingleHome()
  const { services } = useLiveHome()
  const home = resolution?.state === 'ready' ? resolution.home : undefined
  const [name, setName] = useState(home?.name ?? '')
  const [timezone, setTimezone] = useState(home?.timezone ?? 'America/Los_Angeles')
  const [currency, setCurrency] = useState(home?.currency ?? 'USD')
  const update = useMutation({
    mutationFn: () => request(`/api/v1/admin/sites/${home?.id ?? ''}`, json('PUT', {
      revision: home?.revision,
      name,
      timezone,
      currency,
      timezone_change_confirmed: timezone !== home?.timezone,
      reason: 'Home settings updated',
    })),
    onSuccess: async () => { await refresh(); await client.invalidateQueries({ queryKey: ['single-home'] }) },
  })
  if (!home) return <EmptyState title="Home setup required" message="Complete onboarding to create your home." />
  return (
    <>
      <Surface title="Home details" subtitle="The name, timezone, and currency used throughout the app.">
        <form className="form-grid" onSubmit={(event) => { event.preventDefault(); update.mutate() }}>
          <label>Home name<input value={name} onChange={(event) => { setName(event.target.value); }} /></label>
          <label>Timezone<input value={timezone} onChange={(event) => { setTimezone(event.target.value); }} /></label>
          <label>Currency<select value={currency} onChange={(event) => { setCurrency(event.target.value); }}><option>USD</option><option>CAD</option><option>EUR</option><option>GBP</option></select></label>
          <div className="form-actions"><button className="button primary" type="submit" disabled={update.isPending}>Save home</button></div>
          {update.isSuccess && <InlineNotice tone="success">Home settings saved.</InlineNotice>}
        </form>
      </Surface>
      <Surface title="Electric service" subtitle="Utility accounts and rate assignments for this home.">
        {services.length ? services.map((service) => <div className="list-row" key={service.id}><span><strong>{service.name}</strong><small>{service.provider} · Billing day {service.billingDay}</small></span><span className="pill">{service.currentPlan ?? 'Rate not assigned'}</span></div>) : <EmptyState title="No electric service" message="Add one from Billing to calculate home energy costs." />}
      </Surface>
    </>
  )
}

function SensorSettings() {
  const location = useLocation()
  const client = useQueryClient()
  const { sensors, refresh } = useLiveHome()
  const { resolution } = useSingleHome()
  const home = resolution?.state === 'ready' ? resolution.home : undefined
  const [adding, setAdding] = useState(new URLSearchParams(location.search).get('action') === 'add')
  const [selected, setSelected] = useState<string>()
  const firmware = useQuery({ queryKey: ['firmware-releases'], queryFn: () => request<Array<{ id: string; version: string; channel: string; active: boolean }>>('/api/v1/firmware-releases') })
  const maintenance = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => enabled
      ? request(`/api/v1/devices/${id}/maintenance`, json('POST', { until: new Date(Date.now() + 3_600_000).toISOString(), note: 'Owner requested maintenance' }))
      : request(`/api/v1/devices/${id}/maintenance`, { method: 'DELETE' }),
    onSuccess: () => void refresh(),
  })
  const remove = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => request(`/api/v1/admin/devices/${id}/unclaim`, json('POST', {
      reason: 'other',
      confirmation: name,
    })),
    onSuccess: async () => { setSelected(undefined); await client.invalidateQueries({ queryKey: ['sensors'] }) },
  })
  const configure = useMutation({
    mutationFn: ({ id, currentName, currentCt }: { id: string; currentName: string; currentCt: string }) => {
      const newName = prompt('Sensor name', currentName)
      const newCt = prompt('CT rating in amps', currentCt)
      if (!newName || !newCt) throw new Error('Sensor edit cancelled.')
      return request(`/api/v1/devices/${id}/config`, json('POST', {
        settings: { name: newName, ct_rating_amps: newCt },
        acknowledge_ct_rating_change: newCt !== currentCt,
        network_rollback_seconds: 300,
      }))
    },
  })
  const updateFirmware = useMutation({
    mutationFn: ({ releaseId, sensorId }: { releaseId: string; sensorId: string }) => request('/api/v1/firmware-deployments', json('POST', {
      firmware_release_id: releaseId,
      device_ids: [sensorId],
      scheduled_at: new Date().toISOString(),
    })),
  })
  return (
    <>
      <Surface title="Sensors" subtitle="Monitor setup, connectivity, storage, and firmware." action={home && <button className="button primary" type="button" onClick={() => { setAdding(true); }}><Plus /> Add sensor</button>}>
        {!sensors.length ? <EmptyState title="No sensors connected" message="Add an ESP32 sensor to begin monitoring." action={home && <button type="button" className="button primary" onClick={() => { setAdding(true); }}>Connect sensor</button>} /> :
          <div className="stack-list">{sensors.map((sensor) => <article className="sensor-row" key={sensor.id}>
            <span className={`sensor-icon ${sensor.online ? 'online' : ''}`}><Radio /></span>
            <span><strong>{sensor.name}</strong><small>{sensor.monitoredCircuit} · {sensor.firmware ?? 'Firmware unknown'} · last seen {relativeTime(sensor.lastSeenAt)}</small></span>
            <span className={`pill ${sensor.online ? 'success' : 'warning'}`}>{sensor.online ? 'Online' : 'Needs attention'}</span>
            <button className="icon-button" type="button" aria-label={`Manage ${sensor.name}`} onClick={() => { setSelected(selected === sensor.id ? undefined : sensor.id); }}><MoreHorizontal /></button>
            {selected === sensor.id && <div className="row-menu">
              <button type="button" onClick={() => { configure.mutate({ id: sensor.id, currentName: sensor.name, currentCt: sensor.ctRatingAmps }); }}><Gauge /> Edit name and CT rating</button>
              <button type="button" onClick={() => { maintenance.mutate({ id: sensor.id, enabled: true }); }}><Wrench /> Start maintenance test</button>
              <button type="button" onClick={() => { void request(`/api/v1/devices/${sensor.id}/credential-rotation`, json('POST', { overlap_seconds: 3600 })); }}><KeyRound /> Rotate credentials</button>
              {firmware.data?.[0] && <button type="button" onClick={() => { const release = firmware.data[0]; if (release && confirm(`Install signed firmware ${release.version} on ${sensor.name}?`)) updateFirmware.mutate({ releaseId: release.id, sensorId: sensor.id }); }}><RefreshCw /> Update signed firmware</button>}
              <button type="button" className="danger" onClick={() => { if (confirm(`Remove ${sensor.name}? Historical readings will be preserved.`)) remove.mutate({ id: sensor.id, name: sensor.name }) }}><Trash2 /> Remove sensor</button>
            </div>}
          </article>)}</div>}
      </Surface>
      {home && adding && <SensorSetupFlow home={home} onClose={() => { setAdding(false); }} />}
    </>
  )
}

function FamilySettings() {
  const { session } = useAuth()
  const client = useQueryClient()
  const family = useQuery({
    queryKey: ['family'],
    queryFn: () => request('/api/v1/admin/users?include_removed=true', {}, (value) => adaptFamily(value, session?.user?.id)),
  })
  const roles = useQuery({
    queryKey: ['family-roles'],
    queryFn: () => request('/api/v1/admin/roles', {}, adaptFamilyRoles),
  })
  const [adding, setAdding] = useState(false)
  const lifecycle = useMutation({
    mutationFn: ({ member, action }: { member: FamilyMember; action: 'disable' | 'enable' | 'remove' | 'restore' | 'revoke-sessions' }) => {
      const payload = action === 'remove' ? {
        reason: 'Removed by homeowner',
        confirmation: member.email,
        expected_revision: member.revision,
        confirm_high_risk: true,
      } : action === 'restore' ? {
        reason: 'Restored by homeowner for access review',
        expected_revision: member.revision,
        confirm_high_risk: true,
      } : action === 'revoke-sessions' ? undefined : {
        reason: `${action} by homeowner`,
        confirm_high_risk: true,
        expected_revision: member.revision,
      }
      return request(`/api/v1/admin/users/${member.id}/${action}`, json('POST', payload))
    },
    onSuccess: () => void client.invalidateQueries({ queryKey: ['family'] }),
  })
  const changeRole = useMutation({
    mutationFn: ({ member, role }: { member: FamilyMember; role: string }) => request(`/api/v1/admin/users/${member.id}/access`, json('PUT', {
      role_ids: [role],
      all_sites: true,
      site_ids: [],
      expected_revision: member.revision,
      reason: 'Family access role updated',
      confirm_high_risk: role === 'admin',
    })),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['family'] }),
  })
  return (
    <Surface title="Family Access" subtitle="Invite people and choose what they can view or manage." action={<button className="button primary" type="button" onClick={() => { setAdding(true); }}><UserPlus /> Add person</button>}>
      {family.isLoading ? <LoadingState /> : family.error ? <ErrorState error={family.error} retry={() => void family.refetch()} /> :
        <div className="stack-list">{family.data?.map((member) => <div className="list-row family-row" key={member.id}><span className="avatar">{member.name.slice(0, 1).toUpperCase()}</span><span><strong>{member.name}{member.isSelf ? ' (you)' : ''}</strong><small>{member.email} · {member.activeSessions} active sessions · {member.status}</small></span>{member.status !== 'removed' && !member.isSelf && !member.protected ? <select aria-label={`Role for ${member.name}`} value={member.roleIds[0] ?? 'viewer'} onChange={(event) => { changeRole.mutate({ member, role: event.target.value }); }}>{roleOptions(roles.data).map((option) => <option key={option.id} value={option.id}>{homeRoleName(option)}</option>)}</select> : <span className="pill">{member.role}</span>}{!member.isSelf && !member.protected && <div className="inline-actions">{member.status === 'removed' ? <button type="button" className="button secondary" onClick={() => { lifecycle.mutate({ member, action: 'restore' }); }}>Restore</button> : <>{member.status === 'active' ? <button type="button" className="button secondary" onClick={() => { lifecycle.mutate({ member, action: 'disable' }); }}>Disable</button> : <button type="button" className="button secondary" onClick={() => { lifecycle.mutate({ member, action: 'enable' }); }}>Enable</button>}<button type="button" className="button secondary" disabled={!member.activeSessions} onClick={() => { lifecycle.mutate({ member, action: 'revoke-sessions' }); }}>Sign out sessions</button><button type="button" className="button danger" onClick={() => { if (confirm(`Remove ${member.name}? Access and sessions end, but audit history is retained.`)) lifecycle.mutate({ member, action: 'remove' }); }}>Remove</button></>}</div>}</div>)}</div>}
      {adding && <InviteFamily roles={roleOptions(roles.data)} onClose={() => { setAdding(false); }} onSaved={() => void client.invalidateQueries({ queryKey: ['family'] })} />}
    </Surface>
  )
}

function roleOptions(roles?: FamilyRoleOption[]): FamilyRoleOption[] {
  return roles?.filter((role) => !role.archived) ?? [
    { id: 'viewer', name: 'Viewer', description: 'View home data', builtIn: true, archived: false, revision: 1, permissions: [], assignedUserCount: 0 },
    { id: 'operator', name: 'Operator', description: 'Manage everyday home operations', builtIn: true, archived: false, revision: 1, permissions: [], assignedUserCount: 0 },
    { id: 'admin', name: 'Administrator', description: 'Full owner access', builtIn: true, archived: false, revision: 1, permissions: [], assignedUserCount: 0 },
  ]
}

function homeRoleName(role: FamilyRoleOption): string {
  if (role.id === 'admin') return 'Owner'
  if (role.id === 'operator') return 'Family Member'
  return role.id === 'viewer' ? 'Viewer' : role.name
}

function InviteFamily({ roles, onClose, onSaved }: { roles: FamilyRoleOption[]; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState('viewer')
  const save = useMutation({
    mutationFn: () => request('/api/v1/users', json('POST', { display_name: name, email, password, roles: [role], confirm_high_risk: role === 'admin' })),
    onSuccess: () => { onSaved(); onClose() },
  })
  return <div className="modal-backdrop"><form className="modal-card small-modal" role="dialog" aria-modal="true" onSubmit={(event) => { event.preventDefault(); save.mutate() }}><header><div><small>Family Access</small><h2>Add person</h2></div></header><div className="setup-body form-grid single"><label>Name<input required value={name} onChange={(event) => { setName(event.target.value); }} /></label><label>Email<input type="email" autoComplete="off" required value={email} onChange={(event) => { setEmail(event.target.value); }} /></label><label>Temporary password<input type="password" minLength={14} autoComplete="new-password" required value={password} onChange={(event) => { setPassword(event.target.value); }} /></label><label>Access<select value={role} onChange={(event) => { setRole(event.target.value); }}>{roles.map((option) => <option key={option.id} value={option.id}>{homeRoleName(option)}</option>)}</select></label>{save.error && <InlineNotice tone="danger">{save.error.message}</InlineNotice>}</div><footer><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button type="submit" className="button primary">Add person</button></footer></form></div>
}

function NotificationSettings() {
  const client = useQueryClient()
  const { session } = useAuth()
  const { resolution } = useSingleHome()
  const homeId = resolution?.state === 'ready' ? resolution.home.id : undefined
  const canManageRules = session ? hasPermission(session, 'alerts.manage_rules') : false
  const channels = useQuery({ queryKey: ['notification-channels'], queryFn: () => request<Record<string, unknown>[]>('/api/v1/notification-channels') })
  const rules = useQuery({
    queryKey: ['alert-rules'],
    queryFn: () => request<AlertRule[]>('/api/v1/alert-rules'),
  })
  const [advanced, setAdvanced] = useState(false)
  const [form, setForm] = useState({ host: '', port: '587', from: '', recipients: '', username: '', password: '' })
  const save = useMutation({
    mutationFn: () => request('/api/v1/notification-channels', json('POST', {
      name: 'Home email',
      channel_type: 'smtp',
      enabled: true,
      configuration: {
        host: form.host,
        port: Number(form.port),
        from: form.from,
        recipients: form.recipients.split(',').map((value) => value.trim()).filter(Boolean),
        username: form.username || undefined,
        password: form.password || undefined,
        starttls: true,
        event_types: ['power_surge', 'heartbeat_stale', 'sd_failure', 'server_failure'],
      },
    })),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['notification-channels'] }),
  })
  const saveRule = useMutation({
    mutationFn: ({ definition, enabled }: { definition: AlertRuleDefinition; enabled: boolean }) => {
      const selectedSite = definition.siteScoped ? homeId : null
      const existing = rules.data?.find((rule) => rule.rule_type === definition.type && rule.site_id === selectedSite)
      const payload = {
        name: existing?.name ?? definition.name,
        rule_type: definition.type,
        severity: existing?.severity ?? definition.severity,
        enabled,
        site_id: selectedSite,
        device_id: existing?.device_id ?? null,
        debounce_seconds: existing?.debounce_seconds ?? definition.debounce,
        resolve_seconds: existing?.resolve_seconds ?? definition.resolve,
        configuration: existing?.configuration ?? definition.configuration,
      }
      return request(
        existing ? `/api/v1/alert-rules/${existing.id}` : '/api/v1/alert-rules',
        json(existing ? 'PUT' : 'POST', payload),
      )
    },
    onSuccess: () => void client.invalidateQueries({ queryKey: ['alert-rules'] }),
  })
  return (
    <>
      <Surface title="Notifications" subtitle="Choose when this home should get your attention.">
        {rules.isLoading && <LoadingState />}
        {ALERT_RULES.map((definition) => {
          const selectedSite = definition.siteScoped ? homeId : null
          const rule = rules.data?.find((item) => item.rule_type === definition.type && item.site_id === selectedSite)
          return (
            <label className="toggle-row" key={definition.type}>
              <span><strong>{definition.label}</strong><small>{definition.description}</small></span>
              <input
                type="checkbox"
                checked={rule?.enabled ?? false}
                disabled={!canManageRules || saveRule.isPending}
                onChange={(event) => { saveRule.mutate({ definition, enabled: event.target.checked }); }}
              />
            </label>
          )
        })}
        <div className="toggle-row" aria-disabled="true">
          <span><strong>High projected bill</strong><small>Shown when the active electric service supplies a bill-budget threshold.</small></span>
          <span className="pill">Plan dependent</span>
        </div>
        {!canManageRules && <InlineNotice>Only a home owner can change alert rules.</InlineNotice>}
        {saveRule.isSuccess && <InlineNotice tone="success">Notification preference saved.</InlineNotice>}
        {saveRule.error && <InlineNotice tone="danger">{saveRule.error.message}</InlineNotice>}
      </Surface>
      <Surface title="Email delivery" subtitle="SMTP credentials are encrypted on the server and never returned to the browser." action={<button className="button secondary" type="button" onClick={() => { setAdvanced(!advanced); }}>{advanced ? 'Hide setup' : 'Set up email'}</button>}>
        {channels.data?.map((channel) => <div className="list-row" key={String(channel.id)}><Mail /><span><strong>{String(channel.name)}</strong><small>{String(channel.channel_type)} · secrets redacted</small></span><span className="pill success">Configured</span></div>)}
        {advanced && <form className="form-grid" onSubmit={(event) => { event.preventDefault(); save.mutate() }}><label>SMTP host<input required value={form.host} onChange={(event) => { setForm({ ...form, host: event.target.value }); }} /></label><label>Port<input type="number" value={form.port} onChange={(event) => { setForm({ ...form, port: event.target.value }); }} /></label><label>From address<input type="email" required value={form.from} onChange={(event) => { setForm({ ...form, from: event.target.value }); }} /></label><label>Recipients<input required value={form.recipients} onChange={(event) => { setForm({ ...form, recipients: event.target.value }); }} placeholder="you@example.com" /></label><label>Username<input autoComplete="off" value={form.username} onChange={(event) => { setForm({ ...form, username: event.target.value }); }} /></label><label>Password<input type="password" autoComplete="new-password" value={form.password} onChange={(event) => { setForm({ ...form, password: event.target.value }); }} /></label><div className="form-actions"><button className="button primary">Save email delivery</button></div></form>}
      </Surface>
    </>
  )
}

interface AlertRule {
  id: string
  name: string
  rule_type: string
  severity: 'info' | 'warning' | 'error' | 'critical'
  enabled: boolean
  site_id: string | null
  device_id: string | null
  debounce_seconds: number
  resolve_seconds: number
  configuration: Record<string, unknown>
}

interface AlertRuleDefinition {
  type: 'power_surge' | 'heartbeat_stale' | 'rate_source_changed' | 'backup_failure' | 'firmware_failed' | 'worker_failure'
  label: string
  name: string
  description: string
  severity: 'warning' | 'error' | 'critical'
  debounce: number
  resolve: number
  configuration: Record<string, unknown>
  siteScoped: boolean
}

const ALERT_RULES: AlertRuleDefinition[] = [
  {
    type: 'power_surge',
    label: 'Power surge',
    name: 'Whole-home power surge',
    description: 'Alert when whole-home demand remains above 12,000 W for 10 seconds.',
    severity: 'critical',
    debounce: 10,
    resolve: 30,
    configuration: { threshold_watts: '12000' },
    siteScoped: true,
  },
  {
    type: 'heartbeat_stale',
    label: 'Sensor offline',
    name: 'Sensor disconnected',
    description: 'Alert after a signed sensor heartbeat has been missing for 60 seconds.',
    severity: 'error',
    debounce: 60,
    resolve: 30,
    configuration: { stale_seconds: 60 },
    siteScoped: true,
  },
  {
    type: 'rate_source_changed',
    label: 'Rate or tier change',
    name: 'Official rate source changed',
    description: 'Alert when verified utility pricing evidence changes.',
    severity: 'warning',
    debounce: 0,
    resolve: 0,
    configuration: {},
    siteScoped: false,
  },
  {
    type: 'backup_failure',
    label: 'Backup failure',
    name: 'Backup verification failed',
    description: 'Alert when a scheduled backup cannot be verified.',
    severity: 'critical',
    debounce: 0,
    resolve: 0,
    configuration: {},
    siteScoped: false,
  },
  {
    type: 'firmware_failed',
    label: 'Firmware update',
    name: 'Firmware deployment failed',
    description: 'Alert when a signed sensor update does not complete.',
    severity: 'critical',
    debounce: 0,
    resolve: 0,
    configuration: {},
    siteScoped: false,
  },
  {
    type: 'worker_failure',
    label: 'System issue',
    name: 'Server worker unhealthy',
    description: 'Alert when a monitored Power Monitor service is unhealthy.',
    severity: 'critical',
    debounce: 60,
    resolve: 60,
    configuration: {},
    siteScoped: false,
  },
]

function AppearanceSettings() {
  const appearance = useAppearance()
  return (
    <Surface title="Appearance" subtitle="Stored only in this browser.">
      <fieldset className="segmented-field"><legend>Theme</legend>{(['dark', 'light', 'system'] as const).map((value) => <button type="button" key={value} className={appearance.theme === value ? 'active' : ''} onClick={() => { appearance.setTheme(value); }}>{value}</button>)}</fieldset>
      <fieldset className="segmented-field"><legend>Density</legend>{(['comfortable', 'compact'] as const).map((value) => <button type="button" key={value} className={appearance.density === value ? 'active' : ''} onClick={() => { appearance.setDensity(value); }}>{value}</button>)}</fieldset>
      <label>Accent color<input type="color" value={appearance.accent} onChange={(event) => { appearance.setAccent(event.target.value); }} /></label>
      <h3>Home cards</h3>
      <label className="toggle-row"><span><strong>Sensor summary</strong><small>Show the compact sensor status section on Home.</small></span><input type="checkbox" checked={appearance.showSensorsCard} onChange={(event) => { appearance.setShowSensorsCard(event.target.checked); }} /></label>
      <label className="toggle-row"><span><strong>Daily chart</strong><small>Show the simple whole-home energy chart on Home.</small></span><input type="checkbox" checked={appearance.showDailyChart} onChange={(event) => { appearance.setShowDailyChart(event.target.checked); }} /></label>
    </Surface>
  )
}

function DataSettings() {
  const client = useQueryClient()
  const { resolution } = useSingleHome()
  const homeId = resolution?.state === 'ready' ? resolution.home.id : undefined
  const backups = useQuery({ queryKey: ['backups'], queryFn: () => request('/api/v1/backups', {}, adaptBackups) })
  const requests = useQuery({
    queryKey: ['backup-requests'],
    queryFn: () => request<Array<{ id: string; operation: string; status: string; maintenance_required: boolean }>>('/api/v1/backup-requests'),
    refetchInterval: 10_000,
  })
  const exports = useQuery({ queryKey: ['exports'], queryFn: () => request<Record<string, unknown>[]>('/api/v1/exports') })
  const createExport = useMutation({
    mutationFn: () => request('/api/v1/exports', json('POST', { format: 'csv', site_id: homeId, scope: 'whole_home' })),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['exports'] }),
  })
  const createLogs = useMutation({
    mutationFn: () => request('/api/v1/admin/logs/exports', json('POST', {})),
  })
  const submit = useMutation({
    mutationFn: ({ operation, backupId }: { operation: 'create' | 'restore_preflight'; backupId?: string }) => request('/api/v1/backup-requests', json('POST', {
      operation,
      backup_id: backupId,
      confirmation: operation === 'restore_preflight' ? 'VERIFY RESTORE' : undefined,
      idempotency_key: crypto.randomUUID(),
    })),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['backup-requests'] })
      await client.invalidateQueries({ queryKey: ['backups'] })
    },
  })
  return (
    <>
      <Surface title="Data & Backups" subtitle="Local PostgreSQL backups with verification and retention." action={<button className="button primary" type="button" disabled={submit.isPending} onClick={() => { submit.mutate({ operation: 'create' }); }}><DatabaseBackup /> Back up now</button>}>
        <InlineNotice>Nightly backups are created by the isolated backup service. Restore always begins with an automated verification preflight.</InlineNotice>
        {submit.isSuccess && <InlineNotice tone="success">Request queued for the isolated backup service.</InlineNotice>}
        {submit.error && <InlineNotice tone="danger">{submit.error.message}</InlineNotice>}
        {requests.data?.filter((item) => ['queued', 'running'].includes(item.status)).map((item) => <div className="list-row" key={item.id}><RefreshCw className="spin" /><span><strong>{item.operation === 'create' ? 'Creating verified backup' : 'Checking restore readiness'}</strong><small>The isolated backup service is processing this request.</small></span><span className="pill">{item.status}</span></div>)}
        {backups.isLoading ? <LoadingState /> : backups.data?.length ? backups.data.map((backup) => <div className="list-row" key={backup.id}><DatabaseBackup /><span><strong>{new Date(backup.createdAt).toLocaleString()}</strong><small>{backup.verifiedAt ? `Verified ${relativeTime(backup.verifiedAt)}` : 'Awaiting verification'}{backup.sizeBytes ? ` · ${fileSize(backup.sizeBytes)}` : ''}</small></span><span className={`pill ${backup.verifiedAt ? 'success' : 'warning'}`}>{backup.status}</span><button className="button secondary" type="button" disabled={!backup.verifiedAt || submit.isPending} onClick={() => { if (confirm('Verify this backup again and prepare a restore maintenance checkpoint? No live data will be overwritten.')) submit.mutate({ operation: 'restore_preflight', backupId: backup.id }); }}>Restore</button></div>) : <EmptyState title="No backup record yet" message="The scheduled backup service records its first verified run after deployment." />}
      </Surface>
      <Surface title="Exports" subtitle="Download server-generated history and audit files.">
        <div className="list-row"><Gauge /><span><strong>{exports.data?.length ?? 0} export jobs</strong><small>Generated files remain local to this server.</small></span></div>
        <div className="inline-actions"><button className="button secondary" type="button" disabled={createExport.isPending || !homeId} onClick={() => { createExport.mutate(); }}>Export usage</button><button className="button secondary" type="button" disabled={createLogs.isPending} onClick={() => { createLogs.mutate(); }}>Download logs</button></div>
        {(createExport.isSuccess || createLogs.isSuccess) && <InlineNotice tone="success">The local export job was queued.</InlineNotice>}
        {(createExport.error || createLogs.error) && <InlineNotice tone="danger">{createExport.error?.message ?? createLogs.error?.message}</InlineNotice>}
      </Surface>
    </>
  )
}

function AdvancedSettings() {
  const { resolution } = useSingleHome()
  const { services } = useLiveHome()
  const home = resolution?.state === 'ready' ? resolution.home : undefined
  const [detail, setDetail] = useState('health')
  const options = [
    ['health', Gauge, 'System health'],
    ['network', Wifi, 'Network policy'],
    ['rates', RefreshCw, 'Detailed rates'],
    ['topology', Radio, 'Monitoring topology'],
    ['firmware', RefreshCw, 'Firmware'],
    ['interface', FileText, 'Interface text'],
    ['layout', Rows3, 'Status layout'],
    ['logs', DatabaseBackup, 'Application logs'],
    ['security', Shield, 'Permissions & audit'],
  ] as const
  return (
    <>
      <Surface title="Advanced" subtitle="Technical controls are separated from everyday home settings.">
        <div className="detail-picker">{options.map(([id, Icon, label]) => <button type="button" className={detail === id ? 'active' : ''} key={id} onClick={() => { setDetail(id); }}><Icon />{label}</button>)}</div>
      </Surface>
      {detail === 'health' && <HealthDetail />}
      {detail === 'network' && <NetworkDetail />}
      {detail === 'rates' && home && <AdvancedRateSettings home={home} services={services} />}
      {detail === 'topology' && home && <TopologyDetail homeId={home.id} />}
      {detail === 'firmware' && <FirmwareDetail />}
      {detail === 'interface' && <InterfaceTextDetail />}
      {detail === 'layout' && <StatusLayoutDetail />}
      {detail === 'logs' && <LogsDetail />}
      {detail === 'security' && <SecurityDetail />}
    </>
  )
}

function HealthDetail() {
  const health = useQuery({ queryKey: ['advanced-health'], queryFn: () => request('/api/v1/health/ready', {}, adaptHealth) })
  if (health.isLoading) return <LoadingState />
  if (health.error) return <ErrorState error={health.error} retry={() => void health.refetch()} />
  const cssAsset = document.querySelector<HTMLLinkElement>('link[rel="stylesheet"]')?.href.split('/').at(-1) ?? 'development styles'
  return <Surface title="System health">{Object.entries(health.data ?? {}).map(([label, value]) => <div className="list-row" key={label}><span><strong>{label.replaceAll('_', ' ')}</strong></span><span className="pill success">{value ?? 'unknown'}</span></div>)}<div className="list-row"><span><strong>Frontend release</strong><small>Commit {__FRONTEND_COMMIT__}</small></span><span className="pill">v{__FRONTEND_VERSION__}</span></div><div className="list-row"><span><strong>CSS bundle</strong><small>Hashed production asset currently loaded by this browser</small></span><code className="bundle-identity">{cssAsset}</code></div></Surface>
}

function NetworkDetail() {
  const runtime = useQuery({ queryKey: ['network-runtime'], queryFn: () => request<Record<string, unknown>>('/api/v1/admin/network/runtime') })
  return <Surface title="Sensor network policy" subtitle="Signed device authentication remains required in every mode.">{runtime.isLoading ? <LoadingState /> : runtime.error ? <ErrorState error={runtime.error} /> : <pre className="structured-data">{JSON.stringify(runtime.data, null, 2)}</pre>}</Surface>
}

function TopologyDetail({ homeId }: { homeId: string }) {
  const circuits = useQuery({ queryKey: ['circuits', homeId], queryFn: () => request<Record<string, unknown>[]>(`/api/v1/circuits?site_id=${encodeURIComponent(homeId)}`) })
  const aggregates = useQuery({ queryKey: ['aggregates', homeId], queryFn: () => request<Record<string, unknown>[]>(`/api/v1/aggregate-sets?site_id=${encodeURIComponent(homeId)}`) })
  return <Surface title="Monitoring topology" subtitle="Whole-home totals and partial circuits remain server-authoritative and double-count protected.">{circuits.isLoading ? <LoadingState /> : <><div className="list-row"><Radio /><span><strong>{circuits.data?.length ?? 0} monitored circuits</strong><small>{aggregates.data?.length ?? 0} aggregate sets · cost scope applies fixed charges only once</small></span></div>{[...(circuits.data ?? []), ...(aggregates.data ?? [])].map((item, index) => <div className="list-row" key={typeof item.id === 'string' ? item.id : String(index)}><span><strong>{typeof item.name === 'string' ? item.name : 'Monitoring group'}</strong><small>{typeof item.measurement_role === 'string' ? item.measurement_role : typeof item.cost_scope === 'string' ? item.cost_scope : 'server managed'}</small></span></div>)}</>}</Surface>
}

function FirmwareDetail() {
  const releases = useQuery({ queryKey: ['firmware-releases'], queryFn: () => request<Record<string, unknown>[]>('/api/v1/firmware-releases') })
  const deployments = useQuery({ queryKey: ['firmware-deployments'], queryFn: () => request<Record<string, unknown>[]>('/api/v1/firmware-deployments') })
  return <Surface title="Signed firmware" subtitle="Only verified, hardware-compatible Ed25519 releases can be scheduled.">{releases.isLoading ? <LoadingState /> : releases.data?.length ? releases.data.map((release, index) => <div className="list-row" key={typeof release.id === 'string' ? release.id : String(index)}><RefreshCw /><span><strong>{typeof release.version === 'string' ? release.version : 'Firmware release'}</strong><small>{typeof release.channel === 'string' ? release.channel : 'signed'} · {typeof release.hardware_target === 'string' ? release.hardware_target : 'hardware target retained'}</small></span><span className="pill success">{release.verified_at ? 'Verified' : 'Pending'}</span></div>) : <EmptyState title="No firmware releases" message="Upload signed releases through the documented owner workflow before scheduling an OTA update." />}<p>{deployments.data?.length ?? 0} deployment records retained.</p></Surface>
}

function InterfaceTextDetail() {
  const client = useQueryClient()
  const draft = useQuery({ queryKey: ['interface-text-draft'], queryFn: () => request<{ base_revision: number; draft_revision: number; values: Record<string, string> }>('/api/v1/admin/interface-text/draft') })
  const [value, setValue] = useState<string>()
  const editorValue = value ?? (draft.data ? JSON.stringify(draft.data.values, null, 2) : '{}')
  const save = useMutation({
    mutationFn: () => request('/api/v1/admin/interface-text/draft', json('PUT', {
      base_revision: draft.data?.base_revision ?? 0,
      draft_revision: draft.data?.draft_revision || undefined,
      values: JSON.parse(editorValue) as Record<string, string>,
      reason: 'Single Home interface text update',
    })),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['interface-text-draft'] }),
  })
  const publish = useMutation({
    mutationFn: async () => {
      const current = await request<{ base_revision: number; draft_revision: number }>('/api/v1/admin/interface-text/preview', json('POST'))
      return request('/api/v1/admin/interface-text/publish', json('POST', { base_revision: draft.data?.base_revision ?? 0, draft_revision: current.draft_revision, reason: 'Single Home interface text publication', confirm: true }))
    },
  })
  return <Surface title="Interface text" subtitle="Edit approved labels as a draft, preview, then publish an immutable revision.">{draft.isLoading ? <LoadingState /> : <><label>Draft overrides<textarea rows={12} value={editorValue} onChange={(event) => { setValue(event.target.value); }} spellCheck={false} /></label><div className="inline-actions"><button className="button secondary" type="button" onClick={() => { save.mutate(); }}>Save draft</button><button className="button primary" type="button" disabled={!draft.data?.draft_revision} onClick={() => { publish.mutate(); }}>Preview & publish</button></div>{(save.error || publish.error) && <InlineNotice tone="danger">{save.error?.message ?? publish.error?.message}</InlineNotice>}</>}</Surface>
}

function StatusLayoutDetail() {
  const client = useQueryClient()
  const draft = useQuery({ queryKey: ['status-layout-draft'], queryFn: () => request<{ base_revision: number; draft_revision: number; configuration: Record<string, unknown> }>('/api/v1/admin/status-indicators/draft') })
  const [value, setValue] = useState<string>()
  const editorValue = value ?? (draft.data ? JSON.stringify(draft.data.configuration, null, 2) : '{}')
  const save = useMutation({
    mutationFn: async () => {
      const configuration = JSON.parse(editorValue) as Record<string, unknown>
      await request('/api/v1/admin/status-indicators/validate', json('POST', { configuration }))
      return request('/api/v1/admin/status-indicators/draft', json('PUT', { base_revision: draft.data?.base_revision ?? 0, draft_revision: draft.data?.draft_revision || undefined, configuration, reason: 'Single Home status layout update' }))
    },
    onSuccess: () => void client.invalidateQueries({ queryKey: ['status-layout-draft'] }),
  })
  const publish = useMutation({
    mutationFn: async () => {
      await request('/api/v1/admin/status-indicators/preview', json('POST', { page: 'overview', role: 'admin', breakpoint: 'desktop', scenario: 'all_defaults' }))
      return request('/api/v1/admin/status-indicators/publish', json('POST', { base_revision: draft.data?.base_revision ?? 0, draft_revision: draft.data?.draft_revision, reason: 'Single Home status layout publication', confirm: true, confirm_critical: true }))
    },
  })
  return <Surface title="Status layout" subtitle="Existing server-owned visibility and placement revisions remain editable and auditable.">{draft.isLoading ? <LoadingState /> : <><label>Draft configuration<textarea rows={14} value={editorValue} onChange={(event) => { setValue(event.target.value); }} spellCheck={false} /></label><div className="inline-actions"><button className="button secondary" type="button" onClick={() => { save.mutate(); }}>Validate & save draft</button><button className="button primary" type="button" disabled={!draft.data?.draft_revision} onClick={() => { publish.mutate(); }}>Preview & publish</button></div>{(save.error || publish.error) && <InlineNotice tone="danger">{save.error?.message ?? publish.error?.message}</InlineNotice>}</>}</Surface>
}

function LogsDetail() {
  const availability = useQuery({ queryKey: ['log-availability'], queryFn: () => request<Record<string, unknown>>('/api/v1/admin/logs/availability') })
  const create = useMutation({ mutationFn: () => request<Record<string, unknown>>('/api/v1/admin/logs/exports', json('POST', {})) })
  return <Surface title="Application logs" subtitle="Redacted local logs are retained for the configured rolling window." action={<button className="button primary" type="button" onClick={() => { create.mutate(); }}>Create log export</button>}>{availability.isLoading ? <LoadingState /> : <pre className="structured-data">{JSON.stringify(availability.data, null, 2)}</pre>}{create.data && <InlineNotice tone="success">Log export {typeof create.data.id === 'string' ? create.data.id : ''} is {typeof create.data.status === 'string' ? create.data.status : 'queued'}.</InlineNotice>}</Surface>
}

function SecurityDetail() {
  const { session } = useAuth()
  const client = useQueryClient()
  const audit = useQuery({ queryKey: ['audit'], queryFn: () => request<Record<string, unknown>[]>('/api/v1/audit-events?limit=25') })
  const roles = useQuery({ queryKey: ['family-roles'], queryFn: () => request('/api/v1/admin/roles', {}, adaptFamilyRoles) })
  const permissions = useQuery({ queryKey: ['permission-catalog'], queryFn: () => request('/api/v1/admin/permissions', {}, adaptPermissions) })
  const [editingRole, setEditingRole] = useState<FamilyRoleOption | 'new'>()
  const [cloneSource, setCloneSource] = useState<FamilyRoleOption>()
  const canManageRoles = session ? hasPermission(session, 'roles.manage') : false
  const archive = useMutation({
    mutationFn: (role: FamilyRoleOption) => request(`/api/v1/admin/roles/${role.id}/archive`, json('POST', {
      reason: 'Role archived from Single Home settings',
      expected_revision: role.revision,
      confirm_high_risk: true,
    })),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['family-roles'] }),
  })
  const text = (value: unknown, fallback = '') => typeof value === 'string' || typeof value === 'number' ? String(value) : fallback
  return (
    <>
      <Surface
        title="Permissions"
        subtitle="Family-friendly roles cover everyday access. Owners can maintain advanced custom roles here."
        action={canManageRoles && <button className="button primary" type="button" onClick={() => { setEditingRole('new'); }}><Plus /> New custom role</button>}
      >
        {roles.isLoading || permissions.isLoading ? <LoadingState /> : roles.error || permissions.error ? <ErrorState error={roles.error ?? permissions.error} /> :
          <div className="stack-list">{roles.data?.map((role) => <div className="list-row role-row" key={role.id}>
            <Shield />
            <span><strong>{homeRoleName(role)}</strong><small>{role.description} · {role.permissions.length} permissions · {role.assignedUserCount} people</small></span>
            <span className="pill">{role.builtIn ? 'Built in' : role.archived ? 'Archived' : `Revision ${role.revision}`}</span>
            {canManageRoles && !role.archived && <div className="inline-actions">
              {!role.builtIn && <button className="button secondary" type="button" onClick={() => { setEditingRole(role); }}>Edit</button>}
              <button className="button secondary" type="button" onClick={() => { setCloneSource(role); }}>Clone</button>
              {!role.builtIn && <button className="button danger" type="button" disabled={role.assignedUserCount > 0 || archive.isPending} onClick={() => { if (confirm(`Archive ${role.name}? It must not be assigned to anyone.`)) archive.mutate(role); }}>Archive</button>}
            </div>}
          </div>)}</div>}
        {archive.error && <InlineNotice tone="danger">{archive.error.message}</InlineNotice>}
      </Surface>
      <Surface title="Audit log" subtitle="Recent access, configuration, and security events.">
        {audit.data?.map((event, index) => <div className="list-row" key={text(event.id, String(index))}><Shield /><span><strong>{text(event.action, 'Audit event')}</strong><small>{text(event.occurred_at)}</small></span></div>) ?? <LoadingState />}
      </Surface>
      {(editingRole || cloneSource) && permissions.data && <RoleEditor
        source={editingRole === 'new' ? undefined : editingRole ?? cloneSource}
        mode={editingRole === 'new' ? 'create' : editingRole ? 'edit' : 'clone'}
        permissions={permissions.data}
        onClose={() => { setEditingRole(undefined); setCloneSource(undefined); }}
        onSaved={() => { void client.invalidateQueries({ queryKey: ['family-roles'] }); setEditingRole(undefined); setCloneSource(undefined); }}
      />}
    </>
  )
}

function RoleEditor({
  source,
  mode,
  permissions,
  onClose,
  onSaved,
}: {
  source?: FamilyRoleOption
  mode: 'create' | 'edit' | 'clone'
  permissions: PermissionOption[]
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(mode === 'clone' ? `${source?.name ?? 'Role'} copy` : source?.name ?? '')
  const [description, setDescription] = useState(source?.description ?? '')
  const [selected, setSelected] = useState<string[]>(source?.permissions ?? [])
  const [reason, setReason] = useState(mode === 'edit' ? 'Custom role revised' : 'Custom role created')
  const groups = permissions.reduce<Record<string, PermissionOption[]>>((result, permission) => {
    result[permission.group] ??= []
    result[permission.group]?.push(permission)
    return result
  }, {})
  const save = useMutation({
    mutationFn: () => {
      const payload = {
        display_name: name,
        description,
        permissions: selected,
        expected_revision: mode === 'edit' ? source?.revision : undefined,
        reason,
        confirm_high_risk: selected.some((code) => permissions.find((permission) => permission.code === code)?.highRisk),
      }
      const path = mode === 'edit'
        ? `/api/v1/admin/roles/${source?.id ?? ''}`
        : mode === 'clone'
          ? `/api/v1/admin/roles/${source?.id ?? ''}/clone`
          : '/api/v1/admin/roles'
      return request(path, json(mode === 'edit' ? 'PUT' : 'POST', payload))
    },
    onSuccess: onSaved,
  })
  return (
    <div className="modal-backdrop">
      <form className="modal-card role-editor" role="dialog" aria-modal="true" aria-labelledby="role-editor-title" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
        <header><div><small>Advanced permissions</small><h2 id="role-editor-title">{mode === 'edit' ? 'Edit custom role' : mode === 'clone' ? 'Clone role' : 'New custom role'}</h2></div><button className="icon-button" type="button" aria-label="Close role editor" onClick={onClose}>×</button></header>
        <div className="setup-body">
          <div className="form-grid">
            <label>Role name<input required minLength={3} value={name} onChange={(event) => { setName(event.target.value); }} /></label>
            <label>Description<input required minLength={3} value={description} onChange={(event) => { setDescription(event.target.value); }} /></label>
          </div>
          <div className="permission-groups">
            {Object.entries(groups).map(([group, items]) => <fieldset key={group}><legend>{group}</legend>{items.map((permission) => <label className="permission-option" key={permission.code}><input type="checkbox" checked={selected.includes(permission.code)} onChange={(event) => { setSelected(event.target.checked ? [...selected, permission.code] : selected.filter((code) => code !== permission.code)); }} /><span><strong>{permission.label}{permission.highRisk ? ' · High risk' : ''}</strong><small>{permission.description}</small></span></label>)}</fieldset>)}
          </div>
          <label>Audit reason<input value={reason} onChange={(event) => { setReason(event.target.value); }} /></label>
          {save.error && <InlineNotice tone="danger">{save.error.message}</InlineNotice>}
        </div>
        <footer><button className="button secondary" type="button" onClick={onClose}>Cancel</button><button className="button primary" type="submit" disabled={save.isPending || selected.length === 0}>{save.isPending ? 'Saving…' : 'Save role'}</button></footer>
      </form>
    </div>
  )
}
