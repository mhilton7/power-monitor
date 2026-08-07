import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Bell,
  CheckCircle2,
  Clock3,
  DatabaseBackup,
  FlaskConical,
  Gauge,
  HardDrive,
  Home,
  FileText,
  KeyRound,
  Mail,
  MoreHorizontal,
  Palette,
  Plus,
  Radio,
  RefreshCw,
  RotateCcw,
  Rows3,
  Shield,
  Trash2,
  UserPlus,
  Users,
  Wifi,
  Wrench,
  X,
} from 'lucide-react'
import { useRef, useState } from 'react'
import { useLocation, useNavigate } from '../../app/router'
import {
  hasPermission,
  satisfiesPolicy,
  SETTINGS_SECTION_POLICIES,
} from '../../access/permissions'
import type { PermissionPolicy } from '../../access/permissions'
import {
  adaptBackups,
  adaptCircuits,
  adaptFamily,
  adaptFamilyRoles,
  adaptNotificationHistory,
  adaptNotificationSuppressions,
  adaptPermissions,
  adaptSensorStorage,
  adaptSystemHealth,
  adaptTestModeHistory,
  adaptTestModeSensors,
} from '../../api/adapters'
import { ApiError, json, request } from '../../api/client'
import { EmptyState, ErrorState, InlineNotice, LoadingState } from '../../components/feedback/States'
import { Surface } from '../../components/data-display/Surface'
import { DropdownMenu, DropdownMenuItem } from '../../components/overlays/DropdownMenu'
import { ModalLayer } from '../../components/overlays/ModalLayer'
import { SensorSetupFlow } from '../../features/sensors/SensorSetupFlow'
import { MeasurementAssignmentDialog } from '../../features/sensors/MeasurementAssignmentDialog'
import { FirmwareUpdateDialog } from '../../features/firmware/FirmwareUpdateDialog'
import { FirmwareFleetWorkflow } from '../../features/firmware/FirmwareFleetWorkflow'
import { ProtectedChangeDialog } from '../../components/security/ProtectedChangeDialog'
import { chartColorContrast, DEFAULT_CHART_COLORS, useAppearance, type ChartColorKind } from '../../state/AppearanceContext'
import { useAuth } from '../../state/AuthContext'
import { useLiveHome } from '../../state/LiveHomeContext'
import { useSingleHome } from '../../state/SingleHomeContext'
import { useTestMode } from '../../state/TestModeContext'
import type {
  FamilyMember,
  FamilyRoleOption,
  PermissionOption,
  BackupSummary,
  SensorSummary,
  SensorStoragePolicy,
  SensorStorageStatus,
  SystemHealthStatus,
  TestLoadProfile,
} from '../../types/models'
import { dateTime, energy, fileSize, money, power, relativeTime, statusLabel, storageCapacity } from '../../utils/format'
import { AdvancedRateSettings } from '../../features/rates/AdvancedRateSettings'
import { DataResetWorkflow } from '../../features/data-reset/DataResetWorkflow'

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
  const { session } = useAuth()
  const visible = SECTIONS.filter(([key]) => satisfiesPolicy(session, SETTINGS_SECTION_POLICIES[key]))
  const requested = SECTIONS.some(([key]) => key === routeSection) ? routeSection as Section : undefined
  const section = requested && visible.some(([key]) => key === requested) ? requested : visible[0]?.[0]
  return (
    <div className="workspace-page settings-page">
      <header className="page-heading">
        <div><small>Manage your home</small><h1 className="page-title">Settings</h1><p>Update sensors, access, notifications, appearance, and local data.</p></div>
      </header>
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="Settings sections">
          {visible.map(([key, Icon, label]) => <button type="button" key={key} className={section === key ? 'active' : ''} onClick={() => { navigate(`/settings/${key}`); }}><Icon />{label}</button>)}
        </nav>
        <div className="settings-detail">
          {section === 'home' && <HomeSettings />}
          {section === 'sensors' && <SensorSettings />}
          {section === 'family' && <FamilySettings />}
          {section === 'notifications' && <NotificationSettings />}
          {section === 'appearance' && <AppearanceSettings />}
          {section === 'data' && <DataSettings />}
          {section === 'advanced' && <AdvancedSettings />}
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
  const navigate = useNavigate()
  const client = useQueryClient()
  const { sensors, services, refresh } = useLiveHome()
  const { session } = useAuth()
  const { resolution } = useSingleHome()
  const home = resolution?.state === 'ready' ? resolution.home : undefined
  const canManageTopology = Boolean(session && hasPermission(session, 'topology.manage'))
  const canEnroll = hasPermission(session, 'enrollment.manage')
  const canManageDevices = hasPermission(session, 'devices.manage')
  const canRemoveDevices = hasPermission(session, 'devices.remove')
  const canViewFirmware = hasPermission(session, 'firmware.view')
  const canManageFirmware = hasPermission(session, 'firmware.manage')
  const canDeployFirmware = hasPermission(session, 'firmware.deploy')
  const canManageTestMode = hasPermission(session, 'settings.manage')
  const testMode = useTestMode()
  const testSensors = useQuery({
    queryKey: ['sensor-test-mode-sensors'],
    queryFn: () => request('/api/v1/test-mode/sensors', {}, adaptTestModeSensors),
    enabled: Boolean(canManageTestMode && testMode.state?.enabled),
    refetchInterval: testMode.state?.enabled ? 5_000 : false,
  })
  const updateTestSensor = useMutation({
    mutationFn: ({ id, offline }: { id: string; offline: boolean }) => request(
      `/api/v1/test-mode/sensors/${id}`,
      json('PUT', { offline, idempotency_key: crypto.randomUUID() }),
    ),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['sensor-test-mode-sensors'] })
      await testMode.refresh()
    },
  })
  const [adding, setAdding] = useState(canEnroll && new URLSearchParams(location.search).get('action') === 'add')
  const [assignmentSensor, setAssignmentSensor] = useState<SensorSummary>()
  const [storageSensor, setStorageSensor] = useState<SensorSummary>()
  const [firmwareSensor, setFirmwareSensor] = useState<SensorSummary>()
  const [agentSensor, setAgentSensor] = useState<SensorSummary>()
  const assignmentRequested = new URLSearchParams(location.search).get('configuration') === 'measurement-assignment'
  const requestedSensor = assignmentRequested && !assignmentSensor
    ? sensors.find((sensor) => !sensor.circuitId || !sensor.utilityAccountId)
    : undefined
  const activeAssignmentSensor = assignmentSensor ?? requestedSensor
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
    onSuccess: async () => { await client.invalidateQueries({ queryKey: ['sensors'] }) },
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
  const agentCommand = useMutation({
    mutationFn: ({ id, commandType }: { id: string; commandType: 'reboot' | 'sync_now' }) => request(
      `/api/v1/devices/${id}/commands`,
      json('POST', {
        command_type: commandType,
        idempotency_key: `ui:${id}:${commandType}:${crypto.randomUUID()}`,
        expires_in_seconds: 1800,
      }),
    ),
    onSuccess: async (_result, variables) => {
      await client.invalidateQueries({ queryKey: ['agent-status', variables.id] })
    },
  })
  return (
    <>
      <Surface title="Sensors" subtitle="Monitor setup, connectivity, storage, and firmware." action={home && canEnroll && <button className="button primary" type="button" onClick={() => { setAdding(true); }}><Plus /> Add sensor</button>}>
        {!sensors.length ? <EmptyState title="No sensors connected" message="Add an ESP32 sensor to begin monitoring." action={home && canEnroll && <button type="button" className="button primary" onClick={() => { setAdding(true); }}>Connect sensor</button>} /> :
          <div className="stack-list">{sensors.map((sensor) => <article className="sensor-row" key={sensor.id}>
            <span className={`sensor-icon ${sensor.online ? 'online' : ''}`}><Radio /></span>
            <span><strong>{sensor.name}</strong><small>{sensor.monitoredCircuit} · {sensor.firmware ?? 'Firmware unknown'}{canViewFirmware && sensor.firmwareOta ? ` · ${sensor.firmwareOta.state === 'ready' ? 'OTA ready' : sensor.firmwareOta.state.replaceAll('_', ' ')}` : ''} · last seen {relativeTime(sensor.lastSeenAt)}</small></span>
            <span className={`pill ${sensor.online ? 'success' : 'warning'}`}>{sensor.online ? 'Online' : 'Needs attention'}</span>
            <DropdownMenu label={`Manage ${sensor.name}`} triggerClassName="icon-button" menuClassName="row-menu" trigger={<MoreHorizontal />}>
              {canManageTopology && <DropdownMenuItem onSelect={() => { setAssignmentSensor(sensor) }}><Rows3 /> Assign circuit and electric service</DropdownMenuItem>}
              {hasPermission(session, 'storage.view') && <DropdownMenuItem onSelect={() => { setStorageSensor(sensor) }}><HardDrive /> Storage</DropdownMenuItem>}
              {canManageDevices && <DropdownMenuItem onSelect={() => { configure.mutate({ id: sensor.id, currentName: sensor.name, currentCt: sensor.ctRatingAmps }); }}><Gauge /> Edit name and CT rating</DropdownMenuItem>}
              {canManageDevices && <DropdownMenuItem onSelect={() => { maintenance.mutate({ id: sensor.id, enabled: true }); }}><Wrench /> Start maintenance test</DropdownMenuItem>}
              {canManageDevices && <DropdownMenuItem onSelect={() => { void request(`/api/v1/devices/${sensor.id}/credential-rotation`, json('POST', { overlap_seconds: 3600 })); }}><KeyRound /> Rotate credentials</DropdownMenuItem>}
              {sensor.protocolVersion === 'pm-agent/2.0.0' && <DropdownMenuItem onSelect={() => { setAgentSensor(sensor) }}><Wifi /> Headless agent status</DropdownMenuItem>}
              {canManageDevices && sensor.protocolVersion === 'pm-agent/2.0.0' && <DropdownMenuItem onSelect={() => { agentCommand.mutate({ id: sensor.id, commandType: 'sync_now' }) }}><RefreshCw /> Force sync</DropdownMenuItem>}
              {canManageDevices && sensor.protocolVersion === 'pm-agent/2.0.0' && <DropdownMenuItem onSelect={() => { if (confirm(`Reboot ${sensor.name}? Measurements remain stored locally during recovery.`)) agentCommand.mutate({ id: sensor.id, commandType: 'reboot' }) }}><RotateCcw /> Reboot agent</DropdownMenuItem>}
              {canViewFirmware && canManageFirmware && canDeployFirmware && <DropdownMenuItem onSelect={() => { setFirmwareSensor(sensor) }}><RefreshCw /> Update firmware</DropdownMenuItem>}
              {canRemoveDevices && <DropdownMenuItem className="danger" onSelect={() => { if (confirm(`Remove ${sensor.name}? Historical readings will be preserved.`)) remove.mutate({ id: sensor.id, name: sensor.name }) }}><Trash2 /> Remove sensor</DropdownMenuItem>}
            </DropdownMenu>
          </article>)}</div>}
      </Surface>
      {canManageTestMode && testMode.state?.enabled && (
        <Surface
          className="test-mode-surface"
          title="Simulated sensors"
          subtitle="Sensor Test Mode · isolated from enrolled ESP32 devices, alerts, exports, and backups."
          action={<button type="button" className="button secondary" onClick={() => { navigateToTestMode(); }}><FlaskConical /> Manage test mode</button>}
        >
          {testSensors.isLoading ? <LoadingState label="Loading simulated sensors…" /> : testSensors.error ? <ErrorState error={testSensors.error} retry={() => void testSensors.refetch()} /> : testSensors.data?.length ? (
            <div className="stack-list">
              {testSensors.data.map((sensor) => (
                <article className="sensor-row test-sensor-row" key={sensor.id}>
                  <span className={`sensor-icon ${sensor.online ? 'online' : ''}`}><FlaskConical /></span>
                  <span>
                    <strong>{sensor.name} <span className="pill">Test Mode</span></strong>
                    <small>{power(sensor.currentPowerW)} · {energy(sensor.energyKwh)} · synthetic only</small>
                  </span>
                  <span className={`pill ${sensor.online ? 'success' : 'warning'}`}>{sensor.online ? 'Simulated online' : 'Simulated offline'}</span>
                  <button
                    type="button"
                    className="button secondary compact"
                    disabled={updateTestSensor.isPending}
                    onClick={() => { updateTestSensor.mutate({ id: sensor.id, offline: sensor.online }); }}
                  >
                    {sensor.online ? 'Simulate offline' : 'Bring online'}
                  </button>
                </article>
              ))}
            </div>
          ) : <EmptyState compact title="No active simulated sensors" message="Increase the simulated active sensor count in Test Mode settings." />}
        </Surface>
      )}
      {home && canEnroll && adding && <SensorSetupFlow home={home} onClose={() => { setAdding(false); }} />}
      {storageSensor && (
        <SensorStorageDialog
          sensor={storageSensor}
          canManage={hasPermission(session, 'storage.manage')}
          onClose={() => { setStorageSensor(undefined) }}
        />
      )}
      {firmwareSensor && (
        <ModalLayer onRequestClose={() => { setFirmwareSensor(undefined) }}>
          <FirmwareUpdateDialog sensor={firmwareSensor} onClose={() => { setFirmwareSensor(undefined) }} />
        </ModalLayer>
      )}
      {agentSensor && (
        <ModalLayer onRequestClose={() => { setAgentSensor(undefined) }}>
          <HeadlessAgentStatusDialog
            sensor={agentSensor}
            commandPending={agentCommand.isPending}
            commandError={agentCommand.error}
            commandSucceeded={agentCommand.isSuccess}
            onCommand={(commandType) => { agentCommand.mutate({ id: agentSensor.id, commandType }) }}
            onClose={() => { setAgentSensor(undefined) }}
          />
        </ModalLayer>
      )}
      {home && activeAssignmentSensor && (
        <ModalLayer onRequestClose={closeAssignment}>
          <MeasurementAssignmentDialog
            home={home}
            sensor={activeAssignmentSensor}
            services={services}
            onClose={closeAssignment}
            onDone={() => {
              closeAssignment()
              void refresh()
            }}
          />
        </ModalLayer>
      )}
    </>
  )

  function closeAssignment() {
    setAssignmentSensor(undefined)
    if (assignmentRequested) navigate('/settings/sensors', { replace: true })
  }

  function navigateToTestMode() {
    navigate('/settings/advanced/sensor-test-mode')
  }
}

function storageEstimate(status: SensorStorageStatus): string {
  if (status.growthState === 'shrinking_after_cleanup') return 'Storage shrinking after cleanup'
  const days = status.estimatedDaysRemaining
  if (days === undefined) return 'Not enough data'
  if (days >= 365 * 5) return 'Over 5 years'
  if (days >= 365) return `Approximately ${Math.round(days / 30)} months`
  if (days < 7) return 'Less than 7 days'
  return `Approximately ${Math.round(days)} days`
}

interface HeadlessAgentStatus {
  protocol: string
  deviceId?: string
  deviceStatus?: string
  lastSeenAt?: string
  heartbeat?: Record<string, unknown>
  commands: Array<{
    commandId: string
    commandType: string
    state: string
    createdAt?: string
    failureCode?: string
  }>
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function optionalText(value: unknown): string | undefined {
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return undefined
}

function adaptHeadlessAgentStatus(value: unknown): HeadlessAgentStatus {
  const source = asRecord(value)
  const commands = Array.isArray(source.commands) ? source.commands : []
  return {
    protocol: optionalText(source.protocol) ?? 'unknown',
    deviceId: optionalText(source.device_id),
    deviceStatus: optionalText(source.device_status),
    lastSeenAt: optionalText(source.last_seen_at),
    heartbeat: source.heartbeat ? asRecord(source.heartbeat) : undefined,
    commands: commands.map((item) => {
      const command = asRecord(item)
      return {
        commandId: optionalText(command.command_id) ?? 'unknown',
        commandType: optionalText(command.command_type) ?? 'unknown',
        state: optionalText(command.state) ?? 'unknown',
        createdAt: optionalText(command.created_at),
        failureCode: optionalText(command.failure_code),
      }
    }),
  }
}

function HeadlessAgentStatusDialog({
  sensor,
  commandPending,
  commandError,
  commandSucceeded,
  onCommand,
  onClose,
}: {
  sensor: SensorSummary
  commandPending: boolean
  commandError: unknown
  commandSucceeded: boolean
  onCommand: (commandType: 'reboot' | 'sync_now') => void
  onClose: () => void
}) {
  const status = useQuery({
    queryKey: ['agent-status', sensor.id],
    queryFn: () => request(
      `/api/v1/devices/${sensor.id}/agent-status`,
      {},
      adaptHeadlessAgentStatus,
    ),
    refetchInterval: 10_000,
  })
  const heartbeat = status.data?.heartbeat ?? {}
  const wifi = asRecord(heartbeat.wifi)
  const pzem = asRecord(heartbeat.pzem)
  const sd = asRecord(heartbeat.sd)
  const sequences = asRecord(heartbeat.sequences)
  const resources = asRecord(heartbeat.resources)
  const reset = asRecord(heartbeat.reset_operation)
  const ota = asRecord(heartbeat.ota)
  return (
    <section className="modal-card storage-dialog" role="dialog" aria-modal="true" aria-labelledby="headless-agent-title">
      <header className="modal-header">
        <div><small>Outbound-only ESP32-S3</small><h2 id="headless-agent-title">{sensor.name} agent</h2></div>
        <button type="button" className="icon-button" aria-label="Close agent status" onClick={onClose}><X /></button>
      </header>
      {status.isLoading ? <LoadingState label="Loading signed agent evidence…" /> : status.error ? <ErrorState error={status.error} retry={() => { void status.refetch() }} /> : (
        <>
          <div className="settings-grid two-column">
            <div><small>Protocol</small><strong>{status.data?.protocol}</strong></div>
            <div><small>Status</small><strong>{status.data?.deviceStatus ?? 'Unknown'}</strong></div>
            <div><small>Firmware</small><strong>{optionalText(heartbeat.firmware_version) ?? 'Unknown'}</strong></div>
            <div><small>Last heartbeat</small><strong>{relativeTime(status.data?.lastSeenAt)}</strong></div>
            <div><small>Wi-Fi</small><strong>{optionalText(wifi.rssi_dbm) ?? '—'} dBm</strong></div>
            <div><small>Uptime</small><strong>{optionalText(heartbeat.uptime_ms) ?? '—'} ms</strong></div>
            <div><small>PZEM</small><strong>{optionalText(pzem.status) ?? optionalText(pzem.ok) ?? 'Unknown'}</strong></div>
            <div><small>microSD</small><strong>{optionalText(sd.status) ?? optionalText(sd.ok) ?? 'Unknown'}</strong></div>
            <div><small>Backlog</small><strong>{optionalText(sequences.backlog) ?? optionalText(heartbeat.backlog_estimate) ?? '0'}</strong></div>
            <div><small>Next sequence</small><strong>{optionalText(sequences.next_sequence) ?? '—'}</strong></div>
            <div><small>Configuration revision</small><strong>{optionalText(heartbeat.configuration_revision) ?? '—'}</strong></div>
            <div><small>Reset generation</small><strong>{optionalText(heartbeat.reset_generation) ?? '—'}</strong></div>
            <div><small>Free heap</small><strong>{optionalText(resources.free_heap_bytes) ?? '—'} bytes</strong></div>
            <div><small>OTA</small><strong>{optionalText(ota.state) ?? 'Idle'}</strong></div>
          </div>
          {Object.keys(reset).length > 0 && <InlineNotice tone="info">Reset state: {optionalText(reset.state) ?? 'unknown'} · checkpoint {optionalText(reset.checkpoint) ?? 'unknown'}</InlineNotice>}
          <Surface title="Recent commands" subtitle="Durable, signed command delivery and immutable terminal results.">
            {status.data?.commands.length ? status.data.commands.slice(0, 8).map((command) => (
              <div className="list-row" key={command.commandId}>
                <span><strong>{command.commandType.replaceAll('_', ' ')}</strong><small>{relativeTime(command.createdAt)}{command.failureCode ? ` · ${command.failureCode}` : ''}</small></span>
                <span className="pill">{command.state.replaceAll('_', ' ')}</span>
              </div>
            )) : <EmptyState compact title="No commands yet" message="Commands appear here after they are queued." />}
          </Surface>
          {commandError && <ErrorState error={commandError} />}
          {commandSucceeded && <InlineNotice tone="success">Command queued for the next signed heartbeat.</InlineNotice>}
          <footer className="modal-actions">
            <button type="button" className="button secondary" disabled={commandPending} onClick={() => { onCommand('sync_now') }}><RefreshCw /> Force sync</button>
            <button type="button" className="button secondary" disabled={commandPending} onClick={() => { if (confirm(`Reboot ${sensor.name}?`)) onCommand('reboot') }}><RotateCcw /> Reboot</button>
          </footer>
        </>
      )}
    </section>
  )
}

function SensorStorageDialog({
  sensor,
  canManage,
  onClose,
}: {
  sensor: SensorSummary
  canManage: boolean
  onClose: () => void
}) {
  const storage = useQuery({
    queryKey: ['sensor-storage', sensor.id],
    queryFn: () => request(`/api/v1/devices/${sensor.id}/storage`, {}, adaptSensorStorage),
    refetchInterval: 15_000,
  })
  return (
    <div className="modal-backdrop">
      <section className="modal-card storage-dialog" role="dialog" aria-modal="true" aria-labelledby="sensor-storage-title">
        <header>
          <div><small>Protected local history</small><h2 id="sensor-storage-title">{sensor.name} storage</h2></div>
          <button type="button" className="icon-button" aria-label="Close storage settings" onClick={onClose}><X /></button>
        </header>
        <div className="setup-body storage-dialog-body">
          {storage.isLoading ? <LoadingState label="Loading storage evidence…" /> : storage.error ? <ErrorState error={storage.error} retry={() => { void storage.refetch() }} /> : storage.data ? (
            <SensorStorageContent status={storage.data} canManage={canManage} refresh={() => storage.refetch()} />
          ) : null}
        </div>
        <footer><button type="button" className="button secondary" onClick={onClose}>Close</button></footer>
      </section>
    </div>
  )
}

export function SensorStorageContent({
  status,
  canManage,
  refresh,
}: {
  status: SensorStorageStatus
  canManage: boolean
  refresh: () => Promise<unknown>
}) {
  const count = (value: number | undefined) => value ?? 'Unavailable'
  const segmentSummary = status.eligibleSegmentCount !== undefined && status.protectedSegmentCount !== undefined
    ? `${status.eligibleSegmentCount} eligible · ${status.protectedSegmentCount} protected`
    : status.segmentCount !== undefined
      ? `${status.segmentCount} total · eligibility unavailable`
      : 'Unavailable'
  const cleanupSummary = status.lastCleanupAt || status.lastCleanupResult
    ? `${status.lastCleanupAt ? relativeTime(status.lastCleanupAt) : 'Time unavailable'} · ${status.lastCleanupResult ?? 'Result unavailable'}`
    : 'Unavailable'
  const normalizedLastError = status.lastError?.trim().toLowerCase()
  const actionableLastError = normalizedLastError && !['healthy', 'none', 'ok'].includes(normalizedLastError)
    ? status.lastError
    : undefined
  const [policy, setPolicy] = useState<SensorStoragePolicy>(status.desiredPolicy)
  const [reason, setReason] = useState('Administrator reviewed protected storage retention')
  const [cleanupReason, setCleanupReason] = useState('Administrator requested acknowledgement-aware safe cleanup')
  const [confirmation, setConfirmation] = useState('')
  const policyMutation = useMutation({
    mutationFn: () => request(`/api/v1/devices/${status.deviceId}/storage/policy`, json('PUT', {
      retention_mode: policy.retentionMode,
      retention_days: policy.retentionDays,
      minimum_local_history_days: policy.minimumLocalHistoryDays,
      storage_notice_percent: policy.noticePercent,
      storage_warning_percent: policy.warningPercent,
      storage_critical_percent: policy.criticalPercent,
      storage_emergency_percent: policy.emergencyPercent,
      storage_emergency_reserve_bytes: policy.emergencyReserveBytes,
      storage_cleanup_target_percent: policy.cleanupTargetPercent,
      storage_cleanup_target_bytes: policy.cleanupTargetBytes,
      event_retention_days: policy.eventRetentionDays,
      reason,
    })),
    onSuccess: refresh,
  })
  const cleanup = useMutation({
    mutationFn: () => request(`/api/v1/devices/${status.deviceId}/storage/cleanup`, json('POST', { reason: cleanupReason })),
    onSuccess: refresh,
  })
  const prepare = useMutation({
    mutationFn: () => request(`/api/v1/devices/${status.deviceId}/storage/prepare-removal`, json('POST', {
      reason: 'Administrator initiated the safe microSD replacement workflow',
      confirmation,
    })),
    onSuccess: refresh,
  })
  const mutationError = policyMutation.error ?? cleanup.error ?? prepare.error
  return (
    <>
      {!status.available && <InlineNotice tone="warning">Waiting for a signed sensor heartbeat. No storage values are assumed.</InlineNotice>}
      {status.preparedForRemoval && <InlineNotice tone="success">Card prepared. The sensor has unmounted the microSD card; remove it only after safely powering down the sensor.</InlineNotice>}
      <div className="storage-status-heading">
        <span className={`pill ${status.healthy ? 'success' : 'warning'}`}>{statusLabel(status.pressureState)}</span>
        <span>{status.policyPending ? `Policy pending on sensor · desired v${status.desiredConfigVersion}` : `Policy effective · v${status.effectiveConfigVersion}`}</span>
      </div>
      <div className="storage-metric-grid">
        <div><small>Card</small><strong>{status.cardType ?? 'Unknown card'} · {storageCapacity(status.capacityBytes)}</strong></div>
        <div><small>Used</small><strong>{fileSize(status.usedBytes)}{status.freePercent !== undefined ? ` · ${Math.max(0, 100 - status.freePercent).toFixed(1)}%` : ''}</strong></div>
        <div><small>Free</small><strong>{fileSize(status.freeBytes)}{status.freePercent !== undefined ? ` · ${status.freePercent.toFixed(1)}%` : ''}</strong></div>
        <div><small>Estimated remaining</small><strong>{storageEstimate(status)}</strong></div>
        <div><small>Reading acknowledgement</small><strong>{status.serverAckSequence ?? 'Unavailable'} / {status.newestStoredSequence ?? 'Unavailable'}</strong></div>
        <div><small>Unsynchronized readings</small><strong>{status.unsynchronizedCount ?? 'Unavailable'}</strong></div>
        <div><small>Safely reclaimable</small><strong>{fileSize(status.eligibleReclaimableBytes)}</strong></div>
        <div><small>Protected, unacknowledged</small><strong>{fileSize(status.blockedUnacknowledgedBytes)}</strong></div>
        <div><small>Segments</small><strong>{segmentSummary}</strong></div>
        <div><small>Event segments</small><strong>{count(status.eventSegmentCount)}</strong></div>
        <div><small>Temporary artifacts</small><strong>{count(status.temporaryArtifactCount)} temporary · {count(status.exportCount)} exports · {count(status.repairArtifactCount)} repair</strong></div>
        <div><small>Last cleanup</small><strong>{cleanupSummary}</strong></div>
        <div><small>Dropped durable intervals</small><strong>{count(status.droppedIntervalCount)}</strong></div>
      </div>
      {status.cleanupRecoveryRequired && <InlineNotice tone="danger">Cleanup recovery is blocked. The sensor preserves all ambiguous files and remains read-only until the journal is safely repaired.</InlineNotice>}
      {status.cleanupInProgress && <InlineNotice tone="info">Acknowledgement-aware cleanup is running on the sensor StorageTask.</InlineNotice>}
      {(status.droppedIntervalCount ?? 0) > 0 ? (
        <InlineNotice tone="danger">
          <strong>History has an explicit storage gap.</strong>
          <span>
            {status.droppedIntervalCount} interval{status.droppedIntervalCount === 1 ? '' : 's'} could not be durably queued
            {status.firstDroppedIntervalAt ? ` beginning ${relativeTime(status.firstDroppedIntervalAt)}` : ''}.
          </span>
        </InlineNotice>
      ) : null}
      {actionableLastError && <InlineNotice tone="danger">Last storage error: {statusLabel(actionableLastError)}</InlineNotice>}
      <details className="storage-policy" open>
        <summary><strong>Retention and full-card protection</strong><span>Only verified, closed, server-acknowledged segments are eligible.</span></summary>
        <form className="form-grid" onSubmit={(event) => { event.preventDefault(); policyMutation.mutate() }}>
          <label>Mode<select disabled={!canManage} value={policy.retentionMode} onChange={(event) => { setPolicy({ ...policy, retentionMode: event.target.value as SensorStoragePolicy['retentionMode'] }) }}><option value="continuous_protected">Continuous, protected (recommended)</option><option value="strict_age">Strict age, acknowledged only</option><option value="disabled">No automatic retention</option></select></label>
          <label>Retain history (days)<input disabled={!canManage} type="number" min={1} max={3650} value={policy.retentionDays} onChange={(event) => { setPolicy({ ...policy, retentionDays: Number(event.target.value) }) }} /></label>
          <label>Minimum local history (days)<input disabled={!canManage} type="number" min={1} max={3650} value={policy.minimumLocalHistoryDays} onChange={(event) => { setPolicy({ ...policy, minimumLocalHistoryDays: Number(event.target.value) }) }} /></label>
          <label>Event evidence retention (days)<input disabled={!canManage} type="number" min={1} max={3650} value={policy.eventRetentionDays} onChange={(event) => { setPolicy({ ...policy, eventRetentionDays: Number(event.target.value) }) }} /></label>
          <label>Notice / warning (%)<span className="paired-inputs"><input aria-label="Notice percent" disabled={!canManage} type="number" value={policy.noticePercent} onChange={(event) => { setPolicy({ ...policy, noticePercent: Number(event.target.value) }) }} /><input aria-label="Warning percent" disabled={!canManage} type="number" value={policy.warningPercent} onChange={(event) => { setPolicy({ ...policy, warningPercent: Number(event.target.value) }) }} /></span></label>
          <label>Critical / emergency (%)<span className="paired-inputs"><input aria-label="Critical percent" disabled={!canManage} type="number" value={policy.criticalPercent} onChange={(event) => { setPolicy({ ...policy, criticalPercent: Number(event.target.value) }) }} /><input aria-label="Emergency percent" disabled={!canManage} type="number" value={policy.emergencyPercent} onChange={(event) => { setPolicy({ ...policy, emergencyPercent: Number(event.target.value) }) }} /></span></label>
          {canManage && <><label className="span-all">Change reason<input minLength={8} required value={reason} onChange={(event) => { setReason(event.target.value) }} /></label><div className="form-actions"><button className="button primary" disabled={policyMutation.isPending}>Apply storage policy</button></div></>}
        </form>
      </details>
      {canManage && <section className="storage-actions">
        <div><h3>Safe cleanup</h3><p>Queues cleanup on StorageTask. Active, corrupt, unacknowledged, untrusted, and too-recent segments remain protected.</p><label>Reason<input minLength={8} value={cleanupReason} onChange={(event) => { setCleanupReason(event.target.value) }} /></label><button type="button" className="button secondary" disabled={cleanup.isPending || cleanupReason.length < 8} onClick={() => { cleanup.mutate() }}>Run safe cleanup</button></div>
        <div><h3>Prepare card for removal</h3><p>Unmounts the card without formatting, resetting, or changing enrollment. Power down before physically removing it.</p><label>Type {status.deviceName}<input value={confirmation} onChange={(event) => { setConfirmation(event.target.value) }} /></label><button type="button" className="button danger" disabled={prepare.isPending || confirmation !== status.deviceName} onClick={() => { prepare.mutate() }}>Prepare for removal</button></div>
      </section>}
      {mutationError && <InlineNotice tone="danger">{mutationError.message}</InlineNotice>}
      {(policyMutation.isSuccess || cleanup.isSuccess || prepare.isSuccess) && <InlineNotice tone="success">Request saved and queued for signed sensor delivery.</InlineNotice>}
    </>
  )
}

function FamilySettings() {
  const { session } = useAuth()
  const client = useQueryClient()
  const canManageUsers = hasPermission(session, 'users.manage')
  const canDisableUsers = hasPermission(session, 'users.disable')
  const canRemoveUsers = hasPermission(session, 'users.remove')
  const canRestoreUsers = hasPermission(session, 'users.restore')
  const canViewRoles = hasPermission(session, 'roles.view')
  const family = useQuery({
    queryKey: ['family'],
    queryFn: () => request('/api/v1/admin/users?include_removed=true', {}, (value) => adaptFamily(value, session?.user?.id)),
  })
  const roles = useQuery({
    queryKey: ['family-roles'],
    queryFn: () => request('/api/v1/admin/roles', {}, adaptFamilyRoles),
    enabled: canViewRoles,
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
    <Surface title="Family Access" subtitle="Invite people and choose what they can view or manage." action={canManageUsers && <button className="button primary" type="button" onClick={() => { setAdding(true); }}><UserPlus /> Add person</button>}>
      {family.isLoading ? <LoadingState /> : family.error ? <ErrorState error={family.error} retry={() => void family.refetch()} /> :
        <div className="stack-list">{family.data?.map((member) => <div className="list-row family-row" key={member.id}><span className="avatar">{member.name.slice(0, 1).toUpperCase()}</span><span><strong>{member.name}{member.isSelf ? ' (you)' : ''}</strong><small>{member.email} · {member.activeSessions} active sessions · {member.status}</small></span>{canManageUsers && member.status !== 'removed' && !member.isSelf && !member.protected ? <select aria-label={`Role for ${member.name}`} value={member.roleIds[0] ?? 'viewer'} onChange={(event) => { changeRole.mutate({ member, role: event.target.value }); }}>{roleOptions(roles.data).map((option) => <option key={option.id} value={option.id}>{homeRoleName(option)}</option>)}</select> : <span className="pill">{member.role}</span>}{!member.isSelf && !member.protected && <div className="inline-actions">{member.status === 'removed' ? canRestoreUsers && <button type="button" className="button secondary" onClick={() => { lifecycle.mutate({ member, action: 'restore' }); }}>Restore</button> : <>{canDisableUsers && (member.status === 'active' ? <button type="button" className="button secondary" onClick={() => { lifecycle.mutate({ member, action: 'disable' }); }}>Disable</button> : <button type="button" className="button secondary" onClick={() => { lifecycle.mutate({ member, action: 'enable' }); }}>Enable</button>)}{canManageUsers && <button type="button" className="button secondary" disabled={!member.activeSessions} onClick={() => { lifecycle.mutate({ member, action: 'revoke-sessions' }); }}>Sign out sessions</button>}{canRemoveUsers && <button type="button" className="button danger" onClick={() => { if (confirm(`Remove ${member.name}? Access and sessions end, but audit history is retained.`)) lifecycle.mutate({ member, action: 'remove' }); }}>Remove</button>}</>}</div>}</div>)}</div>}
      {canManageUsers && adding && <InviteFamily roles={roleOptions(roles.data)} onClose={() => { setAdding(false); }} onSaved={() => void client.invalidateQueries({ queryKey: ['family'] })} />}
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
  const { alerts } = useLiveHome()
  const { resolution } = useSingleHome()
  const homeId = resolution?.state === 'ready' ? resolution.home.id : undefined
  const canManageRules = session ? hasPermission(session, 'alerts.manage_rules') : false
  const canManageDelivery = hasPermission(session, 'alerts.manage_delivery')
  const canViewAlerts = hasPermission(session, 'alerts.view')
  const channels = useQuery({ queryKey: ['notification-channels'], queryFn: () => request<Record<string, unknown>[]>('/api/v1/notification-channels'), enabled: canManageDelivery })
  const rules = useQuery({
    queryKey: ['alert-rules'],
    queryFn: () => request<AlertRule[]>('/api/v1/alert-rules'),
    enabled: canViewAlerts || canManageRules,
  })
  const suppressions = useQuery({
    queryKey: ['notification-suppressions'],
    queryFn: () => request('/api/v1/notification-suppressions', {}, adaptNotificationSuppressions),
    enabled: canManageDelivery,
  })
  const [historyState, setHistoryState] = useState('')
  const [historySeverity, setHistorySeverity] = useState('')
  const [historyCategory, setHistoryCategory] = useState('')
  const historyQuery = new URLSearchParams({ page_size: '25' })
  if (historyState) historyQuery.set('state', historyState)
  if (historySeverity) historyQuery.set('severity', historySeverity)
  if (historyCategory) historyQuery.set('category', historyCategory)
  const history = useQuery({
    queryKey: ['notification-history', historyState, historySeverity, historyCategory],
    queryFn: () => request(`/api/v1/notification-history?${historyQuery.toString()}`, {}, adaptNotificationHistory),
    enabled: canViewAlerts,
  })
  const attempts = useQuery({
    queryKey: ['notification-attempts'],
    queryFn: () => request<Array<Record<string, unknown>>>('/api/v1/notification-attempts?limit=50'),
    enabled: canManageDelivery,
    refetchInterval: 5_000,
  })
  const [advanced, setAdvanced] = useState(false)
  const [ignoreOpen, setIgnoreOpen] = useState(false)
  const [ignoreConfirmed, setIgnoreConfirmed] = useState(false)
  const [ignoreReason, setIgnoreReason] = useState('')
  const [ignoreScope, setIgnoreScope] = useState<'user' | 'home'>('home')
  const [editingChannelId, setEditingChannelId] = useState<string>()
  const [form, setForm] = useState({ host: '', port: '587', from: '', recipients: '', username: '', password: '' })
  const save = useMutation({
    mutationFn: () => request(editingChannelId ? `/api/v1/notification-channels/${editingChannelId}` : '/api/v1/notification-channels', json(editingChannelId ? 'PUT' : 'POST', {
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
    onSuccess: async () => {
      setAdvanced(false)
      setEditingChannelId(undefined)
      setForm({ host: '', port: '587', from: '', recipients: '', username: '', password: '' })
      await client.invalidateQueries({ queryKey: ['notification-channels'] })
      await client.invalidateQueries({ queryKey: ['alerts'] })
    },
  })
  const disableChannel = useMutation({
    mutationFn: (channelId: string) => request(`/api/v1/notification-channels/${channelId}`, { method: 'DELETE' }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['notification-channels'] })
      await client.invalidateQueries({ queryKey: ['alerts'] })
    },
  })
  const editChannel = (channel: Record<string, unknown>) => {
    const target = channel.target && typeof channel.target === 'object' && !Array.isArray(channel.target) ? channel.target as Record<string, unknown> : {}
    setEditingChannelId(displayUnknown(channel.id, ''))
    setForm({
      host: displayUnknown(target.host, ''),
      port: displayUnknown(target.port, '587'),
      from: displayUnknown(target.from, ''),
      recipients: '',
      username: '',
      password: '',
    })
    setAdvanced(true)
  }
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
  const recommendation = alerts.find((item) => item.code === 'recommendation.smtp_not_configured')
  const suppress = useMutation({
    mutationFn: () => request(`/api/v1/notifications/${encodeURIComponent(recommendation?.id ?? '')}/suppress`, json('POST', { scope: ignoreScope, reason: ignoreReason, confirmed: ignoreConfirmed })),
    onSuccess: async () => {
      setIgnoreOpen(false)
      setIgnoreConfirmed(false)
      await client.invalidateQueries({ queryKey: ['alerts'] })
      await client.invalidateQueries({ queryKey: ['notification-suppressions'] })
    },
  })
  const restore = useMutation({
    mutationFn: ({ id, revision }: { id: string; revision: number }) => request(`/api/v1/notification-suppressions/${id}?expected_revision=${revision}`, { method: 'DELETE' }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['alerts'] })
      await client.invalidateQueries({ queryKey: ['notification-suppressions'] })
    },
  })
  const testChannel = useMutation({
    mutationFn: (channelId: string) => request<{ attempt_id: string }>(`/api/v1/notification-channels/${channelId}/test`, { method: 'POST' }),
    onSuccess: async () => client.invalidateQueries({ queryKey: ['notification-attempts'] }),
  })
  return (
    <>
      <header className="section-heading">
        <div>
          <small>Alerts and delivery</small>
          <h2>Notifications</h2>
          <p>Review alert rules, optional delivery channels, ignored recommendations, and lifecycle history.</p>
        </div>
      </header>
      <Surface title="Alert rules" subtitle="Choose which authoritative conditions open dashboard alerts and configured deliveries.">
        {rules.isLoading && <LoadingState />}
        {ALERT_RULES.map((definition) => {
          const selectedSite = definition.siteScoped ? homeId : null
          const rule = rules.data?.find((item) => item.rule_type === definition.type && item.site_id === selectedSite)
          return (
            <label className="toggle-row" key={definition.type}>
              <span><strong>{definition.label}</strong><small>{definition.description}</small><small>Wait before opening: {definition.debounce}s · Resolve after: {definition.resolve}s healthy · Applies to: {definition.siteScoped ? 'this Home' : 'server'} · Delivery: dashboard and configured channels</small></span>
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
      {canManageDelivery && <Surface title="Delivery" subtitle="SMTP is optional. Dashboard alerts continue even when email delivery is off. Stored credentials are never returned to the browser." action={<button className="button secondary" type="button" onClick={() => { setAdvanced(!advanced); }}>{advanced ? 'Hide setup' : channels.data?.length ? 'Add channel' : 'Set up email'}</button>}>
        {!channels.isLoading && !channels.data?.length && <div className="optional-delivery-card"><Mail aria-hidden="true" /><div><strong>Email delivery is off</strong><p>Power Monitor continues showing alerts in this dashboard. Email setup is optional.</p><div className="inline-actions"><button type="button" className="button primary" onClick={() => { setAdvanced(true); }}>Set up email</button>{recommendation && <button type="button" className="button secondary" onClick={() => { setIgnoreOpen(true); }}>Do not remind me again</button>}</div></div></div>}
        {channels.data?.map((channel) => <NotificationChannelRow key={String(channel.id)} channel={channel} attempts={attempts.data ?? []} testing={testChannel.isPending} mutating={disableChannel.isPending} onTest={(id) => { testChannel.mutate(id); }} onEdit={() => { editChannel(channel); }} onDisable={(id) => { disableChannel.mutate(id); }} />)}
        {testChannel.isPending && <InlineNotice>Test queued: connecting, negotiating TLS, authenticating, and submitting the message.</InlineNotice>}
        {testChannel.isSuccess && <InlineNotice tone="success">Test queued. Delivery progress and the safe final result update above.</InlineNotice>}
        {testChannel.error && <InlineNotice tone="danger">{testChannel.error.message}</InlineNotice>}
        {advanced && <form className="form-grid" onSubmit={(event) => { event.preventDefault(); save.mutate() }}><label>SMTP host<input required value={form.host} onChange={(event) => { setForm({ ...form, host: event.target.value }); }} /></label><label>Port<input type="number" value={form.port} onChange={(event) => { setForm({ ...form, port: event.target.value }); }} /></label><label>From address<input type="email" required value={form.from} onChange={(event) => { setForm({ ...form, from: event.target.value }); }} /></label><label>Recipients<input required value={form.recipients} onChange={(event) => { setForm({ ...form, recipients: event.target.value }); }} placeholder={editingChannelId ? 'Re-enter recipients to confirm this update' : 'you@example.com'} /></label><label>Username<input autoComplete="off" value={form.username} onChange={(event) => { setForm({ ...form, username: event.target.value }); }} placeholder={editingChannelId ? 'Leave blank to preserve' : ''} /></label><label>Password<input type="password" autoComplete="new-password" value={form.password} onChange={(event) => { setForm({ ...form, password: event.target.value }); }} placeholder={editingChannelId ? 'Leave blank to preserve' : ''} /></label><div className="form-actions">{editingChannelId && <button type="button" className="button secondary" onClick={() => { setEditingChannelId(undefined); setAdvanced(false); }}>Cancel edit</button>}<button className="button primary">{editingChannelId ? 'Save channel changes' : 'Save email delivery'}</button></div></form>}
      </Surface>}
      {canManageDelivery && <Surface title="Ignored recommendations" subtitle="Optional reminders hidden for your account or this Home remain audited and reversible.">
        {suppressions.isLoading ? <LoadingState /> : suppressions.error ? <ErrorState error={suppressions.error} retry={() => void suppressions.refetch()} /> : suppressions.data?.length ? <div className="stack-list">{suppressions.data.map((item) => <div className="list-row" key={item.id}><Bell /><span><strong>{item.suppressionKey === 'recommendation.smtp_not_configured' ? 'Email notifications are not configured' : statusLabel(item.suppressionKey)}</strong><small>{statusLabel(item.scopeType)}: {item.scopeName} · Ignored by {item.createdBy} on {dateTime(item.createdAt)}{item.reason ? ` · ${item.reason}` : ''}</small></span><button type="button" className="button secondary" disabled={restore.isPending} onClick={() => { restore.mutate({ id: item.id, revision: item.revision }); }}>Restore reminder</button></div>)}</div> : <EmptyState title="No ignored recommendations" message="Optional reminders you permanently ignore will remain manageable here." />}
      </Surface>}
      {canViewAlerts && <Surface title="Notification history" subtitle="Immutable lifecycle and delivery events. The newest 25 records are shown.">
        <div className="notification-history-filters" aria-label="Notification history filters">
          <label>State<select value={historyState} onChange={(event) => { setHistoryState(event.target.value) }}><option value="">All states</option><option value="opened">Opened</option><option value="acknowledged">Acknowledged</option><option value="silenced">Silenced</option><option value="silence_expired">Silence expired</option><option value="resolved">Resolved</option><option value="reopened">Reopened</option><option value="delivery_failed">Delivery failed</option><option value="permanently_suppressed">Suppressed</option></select></label>
          <label>Severity<select value={historySeverity} onChange={(event) => { setHistorySeverity(event.target.value) }}><option value="">All severities</option><option value="critical">Critical</option><option value="error">Error</option><option value="warning">Warning</option><option value="info">Info</option></select></label>
          <label>Category<input value={historyCategory} onChange={(event) => { setHistoryCategory(event.target.value) }} placeholder="All categories" /></label>
        </div>
        {history.isLoading ? <LoadingState /> : history.error ? <ErrorState error={history.error} retry={() => void history.refetch()} /> : history.data?.items.length ? <div className="notification-history-list" role="table" aria-label="Notification history">{history.data.items.map((item) => <div className="list-row" role="row" key={item.id}><Clock3 /><span><strong>{statusLabel(item.eventType)}</strong><small>{statusLabel(item.category)} · {statusLabel(item.severity)} · {dateTime(item.occurredAt)}{item.actorName ? ` · ${item.actorName}` : ''}</small></span></div>)}</div> : <EmptyState title="No notification history yet" message="Open, acknowledge, silence, resolution, suppression, and delivery events will appear here." />}
        {history.data && history.data.total > history.data.items.length && <small>Showing {history.data.items.length} of {history.data.total} matching immutable events.</small>}
      </Surface>}
      {ignoreOpen && recommendation && <div className="modal-backdrop"><form className="modal-card small-modal" role="dialog" aria-modal="true" aria-labelledby="settings-ignore-title" onSubmit={(event) => { event.preventDefault(); suppress.mutate(); }}><header><div><small>Optional recommendation</small><h2 id="settings-ignore-title">Stop email setup reminders?</h2></div></header><div className="setup-body form-grid single"><p>Dashboard alerts continue normally. No email will be sent until a delivery channel is configured. You can restore this reminder here later.</p><fieldset><legend>Scope</legend><label><input type="radio" name="ignore-scope" checked={ignoreScope === 'home'} onChange={() => { setIgnoreScope('home'); }} /> Do not remind this home again</label><label><input type="radio" name="ignore-scope" checked={ignoreScope === 'user'} onChange={() => { setIgnoreScope('user'); }} /> Dismiss for me</label></fieldset><label>Reason (optional)<textarea value={ignoreReason} onChange={(event) => { setIgnoreReason(event.target.value); }} /></label><label className="check-row"><input type="checkbox" checked={ignoreConfirmed} onChange={(event) => { setIgnoreConfirmed(event.target.checked); }} /> I understand dashboard alerts continue and this reminder can be restored.</label>{suppress.error && <InlineNotice tone="danger">{suppress.error.message}</InlineNotice>}</div><footer><button type="button" className="button secondary" onClick={() => { setIgnoreOpen(false); }}>Cancel</button><button type="submit" className="button primary" disabled={!ignoreConfirmed || suppress.isPending}>Do not remind me again</button></footer></form></div>}
    </>
  )
}

function NotificationChannelRow({ channel, attempts, testing, mutating, onTest, onEdit, onDisable }: { channel: Record<string, unknown>; attempts: Array<Record<string, unknown>>; testing: boolean; mutating: boolean; onTest: (id: string) => void; onEdit: () => void; onDisable: (id: string) => void }) {
  const target = channel.target && typeof channel.target === 'object' && !Array.isArray(channel.target) ? channel.target as Record<string, unknown> : {}
  const lastAttempt = attempts.find((attempt) => String(attempt.channel_id) === String(channel.id))
  return <div className="notification-channel-row"><Mail /><span><strong>{displayUnknown(channel.name, 'Notification channel')}</strong><small>{displayUnknown(target.host, 'SMTP host')}:{displayUnknown(target.port, 'default')} · {target.starttls ? 'STARTTLS' : target.implicit_tls ? 'Implicit TLS' : 'TLS off'} · {displayUnknown(target.from, 'sender unavailable')} · {displayUnknown(target.recipient_count, '0')} recipients · secrets redacted</small>{lastAttempt && <small>Last result: {statusLabel(displayUnknown(lastAttempt.status, 'unknown'))}{lastAttempt.response_summary ? ` · ${displayUnknown(lastAttempt.response_summary, 'Processing')}` : ''}{lastAttempt.safe_error_summary ? ` · ${displayUnknown(lastAttempt.safe_error_summary, 'Delivery failed')}` : ''}{lastAttempt.next_attempt_at ? ` · retry ${dateTime(displayUnknown(lastAttempt.next_attempt_at, ''))}` : ''}</small>}</span><div className="inline-actions"><span className={`pill ${channel.enabled ? 'success' : ''}`}>{channel.enabled ? 'Enabled' : 'Disabled'}</span><button type="button" className="button secondary compact" onClick={onEdit}>Edit</button>{Boolean(channel.enabled) && <button type="button" className="button secondary compact" disabled={mutating} onClick={() => { onDisable(displayUnknown(channel.id, '')); }}>Disable</button>}<button type="button" className="button secondary compact" disabled={testing || !channel.enabled} onClick={() => { onTest(displayUnknown(channel.id, '')); }}>Test email</button></div></div>
}

function displayUnknown(value: unknown, fallback: string): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : fallback
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

function ChartColorControl({ kind, color, onChange, disabled = false }: { kind: ChartColorKind; color: string; onChange: (color: string) => void; disabled?: boolean }) {
  const [draft, setDraft] = useState<string>()
  const label = kind === 'cost' ? 'Estimated cost' : kind.charAt(0).toUpperCase() + kind.slice(1)
  const updateDraft = (value: string) => {
    setDraft(value)
    if (/^#[0-9a-f]{6}$/iu.test(value)) onChange(value)
  }
  return (
    <div className="chart-color-row">
      <span>{label}</span>
      <label className="chart-color-swatch"><span className="sr-only">{label} color picker</span><input type="color" value={color} disabled={disabled} onChange={(event) => { onChange(event.target.value) }} /></label>
      <label className="chart-color-hex"><span className="sr-only">{label} hexadecimal color</span><input aria-invalid={draft !== undefined && !/^#[0-9a-f]{6}$/iu.test(draft)} inputMode="text" maxLength={7} pattern="#[0-9A-Fa-f]{6}" value={draft ?? color} disabled={disabled} onChange={(event) => { updateDraft(event.target.value) }} onBlur={() => { setDraft(undefined) }} /></label>
      <i className="chart-color-preview" aria-hidden="true" style={{ backgroundColor: color }} />
    </div>
  )
}

function AppearanceSettings() {
  const appearance = useAppearance()
  const { session } = useAuth()
  const canPublish = hasPermission(session, 'settings.manage')
  const [colorOverrides, setColorOverrides] = useState<Partial<Record<ChartColorKind, string>>>({})
  const draftColors = { ...appearance.chartColors, ...colorOverrides }
  const colorsDirty = Object.keys(colorOverrides).length > 0
  const publishColors = useMutation({
    mutationFn: () => appearance.publishChartColors(draftColors),
    onSuccess: () => { setColorOverrides({}) },
  })
  const chartBackground = appearance.theme === 'light' ? '#FFFFFF' : '#10251D'
  return (
    <Surface title="Appearance" subtitle="Personal display options and administrator-published chart colors.">
      <fieldset className="segmented-field"><legend>Theme</legend>{(['dark', 'light', 'system'] as const).map((value) => <button type="button" key={value} className={appearance.theme === value ? 'active' : ''} onClick={() => { appearance.setTheme(value); }}>{value}</button>)}</fieldset>
      <fieldset className="segmented-field"><legend>Density</legend>{(['comfortable', 'compact'] as const).map((value) => <button type="button" key={value} className={appearance.density === value ? 'active' : ''} onClick={() => { appearance.setDensity(value); }}>{value}</button>)}</fieldset>
      <label>Accent color<input type="color" value={appearance.accent} onChange={(event) => { appearance.setAccent(event.target.value); }} /></label>
      <div className="chart-color-settings">
        <div className="surface-header"><div><h3>History chart colors</h3><p>Published by an administrator and applied to Home and History charts for every user.</p></div><div className="inline-actions"><button type="button" className="button secondary" disabled={!canPublish || publishColors.isPending} onClick={() => { setColorOverrides({ ...DEFAULT_CHART_COLORS }) }}>Reset colors</button><button type="button" className="button primary" disabled={!canPublish || !colorsDirty || publishColors.isPending} onClick={() => { publishColors.mutate() }}>{publishColors.isPending ? 'Applying…' : 'Apply colors'}</button></div></div>
        {(['power', 'energy', 'cost'] as const).map((kind) => (
          <ChartColorControl key={kind} kind={kind} color={draftColors[kind]} disabled={!canPublish} onChange={(value) => { setColorOverrides((current) => ({ ...current, [kind]: value.toUpperCase() })) }} />
        ))}
        {Object.values(draftColors).some((color) => chartColorContrast(color, chartBackground) < 3) && <InlineNotice tone="warning">One or more chart lines have low contrast in the selected theme. The color is preserved; choose a more distinct color for easier reading.</InlineNotice>}
        {publishColors.isSuccess && <InlineNotice tone="success">Chart colors applied for every user.</InlineNotice>}
        {publishColors.error && <InlineNotice tone="danger">{publishColors.error.message}</InlineNotice>}
        {!canPublish && <InlineNotice>Only an administrator can change the shared chart colors.</InlineNotice>}
        <small>Choose colors that remain distinguishable against the chart background. Cost is also rendered with a dashed line.</small>
      </div>
      <h3>Home cards</h3>
      <label className="toggle-row"><span><strong>Sensor summary</strong><small>Show the compact sensor status section on Home.</small></span><input type="checkbox" checked={appearance.showSensorsCard} onChange={(event) => { appearance.setShowSensorsCard(event.target.checked); }} /></label>
      <label className="toggle-row"><span><strong>Daily chart</strong><small>Show the simple whole-home energy chart on Home.</small></span><input type="checkbox" checked={appearance.showDailyChart} onChange={(event) => { appearance.setShowDailyChart(event.target.checked); }} /></label>
    </Surface>
  )
}

function DataSettings() {
  const { session } = useAuth()
  const canCreate = hasPermission(session, 'backups.create')
  const canVerify = hasPermission(session, 'backups.verify')
  const canDeleteBackups = hasPermission(session, 'backups.delete')
  const canRestore = hasPermission(session, 'backups.restore')
  const canExportHistory = hasPermission(session, 'history.export')
  const canExportLogs = hasPermission(session, 'logs.export')
  const client = useQueryClient()
  const { resolution } = useSingleHome()
  const homeId = resolution?.state === 'ready' ? resolution.home.id : undefined
  const createKey = useRef<string | null>(null)
  const [selectedBackup, setSelectedBackup] = useState<BackupSummary>()
  const [replaceDialogOpen, setReplaceDialogOpen] = useState(false)
  const [replaceConfirmation, setReplaceConfirmation] = useState('')
  const backups = useQuery({ queryKey: ['backups'], queryFn: () => request('/api/v1/backups', {}, adaptBackups) })
  const replacePreview = useQuery({
    queryKey: ['backups', 'replace-all-preview'],
    queryFn: () => request<{
      existing_backup_count: number
      existing_storage_bytes: number
      incomplete_backup_count: number
      unverified_backup_count: number
      verified_backup_count: number
      estimated_reclaim_bytes: number
    }>('/api/v1/backups/replace-all-preview'),
    enabled: replaceDialogOpen && canCreate && canDeleteBackups,
  })
  const requests = useQuery({
    queryKey: ['backup-requests'],
    queryFn: () => request<Array<{
      id: string
      operation: string
      status: string
      backup_id?: string
      maintenance_required: boolean
      progress?: Record<string, unknown>
      result?: Record<string, unknown>
    }>>('/api/v1/backup-requests'),
    refetchInterval: 10_000,
  })
  const exports = useQuery({ queryKey: ['exports'], queryFn: () => request<Record<string, unknown>[]>('/api/v1/exports'), enabled: canExportHistory })
  const createExport = useMutation({
    mutationFn: () => request('/api/v1/exports', json('POST', { format: 'csv', site_id: homeId, scope: 'whole_home' })),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['exports'] }),
  })
  const createLogs = useMutation({
    mutationFn: () => request('/api/v1/admin/logs/exports', json('POST', {})),
  })
  const submit = useMutation({
    mutationFn: ({ operation, backupId, idempotencyKey }: { operation: 'create' | 'restore_preflight'; backupId?: string; idempotencyKey: string }) => request('/api/v1/backup-requests', json('POST', {
      operation,
      backup_id: backupId,
      confirmation: operation === 'restore_preflight' ? 'VERIFY RESTORE' : undefined,
      idempotency_key: idempotencyKey,
    })),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['backup-requests'] })
      await client.invalidateQueries({ queryKey: ['backups'] })
    },
    onSettled: () => {
      createKey.current = null
    },
  })
  const verify = useMutation({
    mutationFn: (backupId: string) => request(
      `/api/v1/backups/${backupId}/verify`,
      json('POST', { idempotency_key: crypto.randomUUID() }),
      (value) => adaptBackups([value])[0],
    ),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['backup-requests'] })
      await client.invalidateQueries({ queryKey: ['backups'] })
    },
  })
  const remove = useMutation({
    mutationFn: ({ backupId, shortId, reason }: { backupId: string; shortId: string; reason: string }) => request(
      `/api/v1/backups/${backupId}`,
      json('DELETE', {
        confirmation: 'DELETE',
        backup_id_confirmation: shortId,
        reason,
      }),
      (value) => adaptBackups([value])[0],
    ),
    onSuccess: async () => {
      setSelectedBackup(undefined)
      await client.invalidateQueries({ queryKey: ['backup-requests'] })
      await client.invalidateQueries({ queryKey: ['backups'] })
    },
  })
  const replaceAll = useMutation({
    mutationFn: () => request('/api/v1/backups/replace-all', json('POST', {
      confirmation: replaceConfirmation,
      idempotency_key: crypto.randomUUID(),
    })),
    onSuccess: async () => {
      setReplaceDialogOpen(false)
      setReplaceConfirmation('')
      await client.invalidateQueries({ queryKey: ['backup-requests'] })
      await client.invalidateQueries({ queryKey: ['backups'] })
    },
  })
  const activeRequests = requests.data?.filter((item) => ['queued', 'running'].includes(item.status)) ?? []
  const operationPending = submit.isPending || verify.isPending || remove.isPending || replaceAll.isPending || activeRequests.length > 0
  const verifiedCount = backups.data?.filter((backup) => backup.verifiedAt && !backup.deletedAt).length ?? 0
  const requestDelete = (backup: BackupSummary) => {
    if (backup.verifiedAt && verifiedCount <= 1) return
    const word = prompt(`Type DELETE to remove backup ${backup.id.slice(0, 8)}. Audit history is preserved.`)
    if (word !== 'DELETE') return
    const shortId = prompt(`Type the backup ID prefix exactly: ${backup.id.slice(0, 8)}`)
    if (shortId !== backup.id.slice(0, 8)) return
    const reason = prompt('Reason for deletion (required):')
    if (!reason || reason.trim().length < 3) return
    remove.mutate({ backupId: backup.id, shortId, reason: reason.trim() })
  }
  const createBackup = () => {
    if (operationPending || createKey.current) return
    createKey.current = crypto.randomUUID()
    submit.mutate({
      operation: 'create',
      idempotencyKey: createKey.current,
    })
  }
  return (
    <>
      <Surface title="Data & Backups" subtitle="Local PostgreSQL backups with isolated restore verification and protected retention." action={canCreate && <button className="button primary" type="button" disabled={operationPending} onClick={createBackup}><DatabaseBackup /> Back up now</button>}>
        <InlineNotice>Nightly backups are created by the isolated backup service. Restore always begins with an automated verification preflight.</InlineNotice>
        {submit.isSuccess && <InlineNotice tone="success">Request queued for the isolated backup service.</InlineNotice>}
        {(submit.error || verify.error || remove.error || replaceAll.error) && <InlineNotice tone="danger">{submit.error?.message ?? verify.error?.message ?? remove.error?.message ?? replaceAll.error?.message}</InlineNotice>}
        {activeRequests.map((item) => <div className="list-row" key={item.id}><RefreshCw className="spin" /><span><strong>{backupOperationLabel(item.operation)}</strong><small>{typeof item.progress?.message === 'string' ? item.progress.message : 'The isolated backup service is processing one global backup operation.'}</small></span><span className="pill">{item.status === 'queued' ? 'Queued' : 'Running'}</span></div>)}
        {backups.isLoading ? <LoadingState /> : backups.data?.length ? backups.data.map((backup) => {
          const canDelete = backupDeleteEligible(backup.status) && !(backup.verifiedAt && verifiedCount <= 1)
          return (
            <div className="list-row backup-row" key={backup.id}>
              <DatabaseBackup />
              <span>
                <strong>{new Date(backup.createdAt).toLocaleString()}</strong>
                <small>
                  {backupStatusDescription(backup)}
                  {backup.sizeBytes !== undefined ? ` · ${fileSize(backup.sizeBytes)}` : ''}
                </small>
              </span>
              <span className={`pill ${backupStatusTone(backup.status)}`}>{backupStatusLabel(backup.status)}</span>
              <div className="inline-actions">
                <button className="button secondary compact" type="button" onClick={() => { setSelectedBackup(backup); }}>Details</button>
                {canVerify && ['completed_unverified', 'verification_failed'].includes(backup.status) && <button className="button secondary compact" type="button" disabled={operationPending} onClick={() => { verify.mutate(backup.id); }}>{backup.status === 'verification_failed' ? 'Retry verification' : 'Verify now'}</button>}
                {backup.status === 'verified' && <>
                  {canVerify && <button className="button secondary compact" type="button" disabled={operationPending} onClick={() => { verify.mutate(backup.id); }}>Verify again</button>}
                  {canRestore && <button className="button secondary compact" type="button" disabled={operationPending} onClick={() => {
                    if (confirm('Run a fresh isolated restore verification and prepare a restore maintenance checkpoint? Production data will not be overwritten.')) {
                      submit.mutate({ operation: 'restore_preflight', backupId: backup.id, idempotencyKey: crypto.randomUUID() })
                    }
                  }}>Restore</button>}
                </>}
                {canDeleteBackups && backupDeleteEligible(backup.status) && <button className="button danger compact" type="button" disabled={operationPending || !canDelete} title={!canDelete ? 'The last verified backup is protected' : undefined} onClick={() => { requestDelete(backup); }}><Trash2 /> {backup.status === 'deletion_failed' ? 'Retry cleanup' : 'Delete'}</button>}
              </div>
            </div>
          )
        }) : <EmptyState title="No backup record yet" message="The scheduled backup service records its first verified run after deployment." />}
      </Surface>
      {canCreate && canDeleteBackups && <Surface title="Advanced backup actions" subtitle="Protected operations that affect every local recovery point.">
        <div className="list-row">
          <DatabaseBackup />
          <span><strong>Replace all backups with one new backup</strong><small>Create and fully restore-test the replacement before older artifacts are removed.</small></span>
          <button className="button danger" type="button" disabled={operationPending} onClick={() => { setReplaceDialogOpen(true); }}>Replace all backups</button>
        </div>
      </Surface>}
      {selectedBackup && <BackupDetails backup={selectedBackup} onClose={() => { setSelectedBackup(undefined); }} />}
      {replaceDialogOpen && (
        <ModalLayer onRequestClose={() => { if (!replaceAll.isPending) setReplaceDialogOpen(false) }}>
          <section className="modal-card backup-replace-dialog" role="dialog" aria-modal="true" aria-labelledby="replace-backups-title">
            <header><div><small>Owner-only destructive action</small><h2 id="replace-backups-title">Replace all backups</h2></div><button className="icon-button" type="button" aria-label="Close replace backups dialog" disabled={replaceAll.isPending} onClick={() => { setReplaceDialogOpen(false); }}>×</button></header>
            <p>This will create and verify one new backup.</p>
            <p>Only after the new backup passes a complete temporary PostgreSQL restore will all older backup artifacts be removed.</p>
            <InlineNotice>If creation or verification fails, no existing backup will be deleted.</InlineNotice>
            {replacePreview.isLoading ? <LoadingState label="Inventorying current backups…" /> : replacePreview.error ? <ErrorState error={replacePreview.error} retry={() => void replacePreview.refetch()} /> : replacePreview.data && (
              <dl className="detail-list">
                <dt>Existing backup count</dt><dd>{replacePreview.data.existing_backup_count}</dd>
                <dt>Existing backup storage usage</dt><dd>{fileSize(replacePreview.data.existing_storage_bytes)}</dd>
                <dt>Incomplete backup count</dt><dd>{replacePreview.data.incomplete_backup_count}</dd>
                <dt>Unverified backup count</dt><dd>{replacePreview.data.unverified_backup_count}</dd>
                <dt>Verified backup count</dt><dd>{replacePreview.data.verified_backup_count}</dd>
                <dt>Estimated storage to reclaim</dt><dd>{fileSize(replacePreview.data.estimated_reclaim_bytes)}</dd>
              </dl>
            )}
            <label>Type <strong>REPLACE ALL BACKUPS</strong> to continue<input autoComplete="off" value={replaceConfirmation} onChange={(event) => { setReplaceConfirmation(event.target.value); }} /></label>
            <footer><button className="button secondary" type="button" disabled={replaceAll.isPending} onClick={() => { setReplaceDialogOpen(false); }}>Cancel</button><button className="button danger" type="button" disabled={replaceAll.isPending || replaceConfirmation !== 'REPLACE ALL BACKUPS' || !replacePreview.data || replacePreview.data.incomplete_backup_count > 0} onClick={() => { replaceAll.mutate(); }}>{replaceAll.isPending ? 'Starting…' : 'Replace all backups'}</button></footer>
          </section>
        </ModalLayer>
      )}
      {(canExportHistory || canExportLogs) && <Surface title="Exports" subtitle="Download server-generated history and audit files.">
        <div className="list-row"><Gauge /><span><strong>{exports.data?.length ?? 0} export jobs</strong><small>Generated files remain local to this server.</small></span></div>
        <div className="inline-actions">{canExportHistory && <button className="button secondary" type="button" disabled={createExport.isPending || !homeId} onClick={() => { createExport.mutate(); }}>Export usage</button>}{canExportLogs && <button className="button secondary" type="button" disabled={createLogs.isPending} onClick={() => { createLogs.mutate(); }}>Download logs</button>}</div>
        {(createExport.isSuccess || createLogs.isSuccess) && <InlineNotice tone="success">The local export job was queued.</InlineNotice>}
        {(createExport.error || createLogs.error) && <InlineNotice tone="danger">{createExport.error?.message ?? createLogs.error?.message}</InlineNotice>}
      </Surface>}
    </>
  )
}

const BACKUP_STATUS_LABELS: Record<string, string> = {
  queued: 'Queued',
  creating: 'Creating backup',
  completed_unverified: 'Verification pending',
  verification_queued: 'Verification queued',
  verifying: 'Verifying',
  verified: 'Verified',
  backup_failed: 'Backup failed',
  verification_failed: 'Verification failed',
  deleting: 'Deleting',
  deletion_failed: 'Deletion failed',
  deleted: 'Deleted',
  restore_preflight: 'Restore preflight',
  restoring: 'Restoring',
  restore_failed: 'Restore failed',
}

function backupStatusLabel(status: string) {
  return BACKUP_STATUS_LABELS[status] ?? 'Unknown'
}

function backupStatusTone(status: string) {
  if (status === 'verified') return 'success'
  if (['backup_failed', 'verification_failed', 'deletion_failed', 'restore_failed'].includes(status)) return 'danger'
  if (status === 'deleted') return ''
  return 'warning'
}

function backupStatusDescription(backup: BackupSummary) {
  if (backup.safeErrorSummary) return backup.safeErrorSummary
  if (backup.verifiedAt) return `Verified ${relativeTime(backup.verifiedAt)}`
  if (backup.status === 'deleted') return 'Artifacts removed; audit history retained'
  return backupStatusLabel(backup.status)
}

function backupOperationLabel(operation: string) {
  return {
    create: 'Creating backup',
    verify: 'Verifying backup',
    restore_preflight: 'Checking restore readiness',
    delete: 'Deleting backup',
    replace_all: 'Replacing all backups',
  }[operation] ?? 'Processing backup'
}

function backupDeleteEligible(status: string) {
  return ['verified', 'completed_unverified', 'verification_failed', 'backup_failed', 'artifact_missing', 'deletion_failed'].includes(status)
}

function BackupDetails({ backup, onClose }: { backup: BackupSummary; onClose: () => void }) {
  const details = backup.verificationDetails
  return (
    <ModalLayer onRequestClose={onClose}>
      <section className="modal-card backup-details-dialog" role="dialog" aria-modal="true" aria-labelledby="backup-details-title">
        <header><div><small>Local restore point</small><h2 id="backup-details-title">Backup details</h2></div><button className="icon-button" type="button" aria-label="Close backup details" onClick={onClose}>×</button></header>
        <dl className="detail-list">
          <dt>Backup ID</dt><dd>{backup.id}</dd>
          <dt>Created</dt><dd>{new Date(backup.createdAt).toLocaleString()}</dd>
          <dt>Completed</dt><dd>{backup.completedAt ? new Date(backup.completedAt).toLocaleString() : 'Not completed'}</dd>
          <dt>Verified</dt><dd>{backup.verifiedAt ? new Date(backup.verifiedAt).toLocaleString() : 'Not verified'}</dd>
          <dt>Status</dt><dd>{backupStatusLabel(backup.status)}</dd>
          <dt>Size</dt><dd>{fileSize(backup.sizeBytes)}</dd>
          <dt>Encryption</dt><dd>{backup.encrypted ? 'Encrypted' : 'Not encrypted'}</dd>
          <dt>Manifest fingerprint</dt><dd>{backup.manifestFingerprint ?? 'Unavailable'}</dd>
          <dt>Verification attempts</dt><dd>{backup.verificationAttempts}</dd>
          <dt>Migration revision</dt><dd>{backupDetailValue(details.migration_revision)}</dd>
          <dt>Table count</dt><dd>{backupDetailValue(details.table_count)}</dd>
          {backup.failedStage && <><dt>Failed stage</dt><dd>{backup.failedStage}</dd></>}
          {backup.safeErrorCode && <><dt>Error code</dt><dd>{backup.safeErrorCode}</dd></>}
          {backup.safeErrorSummary && <><dt>Safe error</dt><dd>{backup.safeErrorSummary}</dd></>}
        </dl>
        <footer><button className="button secondary" type="button" onClick={onClose}>Close</button></footer>
      </section>
    </ModalLayer>
  )
}

function backupDetailValue(value: unknown) {
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
    ? String(value)
    : 'Unavailable'
}

const ADVANCED_DETAIL_POLICIES = {
  'system-health': { allOf: ['settings.manage'] },
  'sensor-test-mode': { allOf: ['settings.manage'] },
  network: { allOf: ['network.manage'] },
  rates: { anyOf: ['rates.manage_custom', 'rates.manage_sources', 'rates.check_sources'] },
  topology: { allOf: ['topology.manage'] },
  firmware: { anyOf: ['firmware.view', 'firmware.manage', 'firmware.deploy'] },
  interface: { allOf: ['interface_text.manage'] },
  layout: { allOf: ['status_indicators.manage'] },
  logs: { allOf: ['logs.export'] },
  security: { allOf: ['audit.view'] },
  'data-reset': { allOf: ['system.data_reset'] },
} as const satisfies Record<string, PermissionPolicy>

function AdvancedSettings() {
  const { session } = useAuth()
  const { resolution } = useSingleHome()
  const { services } = useLiveHome()
  const location = useLocation()
  const navigate = useNavigate()
  const home = resolution?.state === 'ready' ? resolution.home : undefined
  const routeDetail = location.pathname.split('/')[3]
  const options = [
    ['system-health', Gauge, 'System health'],
    ['sensor-test-mode', FlaskConical, 'Sensor Test Mode'],
    ['network', Wifi, 'Network policy'],
    ['rates', RefreshCw, 'Detailed rates'],
    ['topology', Radio, 'Monitoring topology'],
    ['firmware', RefreshCw, 'Firmware'],
    ['interface', FileText, 'Interface text'],
    ['layout', Rows3, 'Status layout'],
    ['logs', DatabaseBackup, 'Application logs'],
    ['security', Shield, 'Permissions & audit'],
    ['data-reset', RotateCcw, 'Data reset'],
  ] as const
  const permittedOptions = options.filter(([id]) => satisfiesPolicy(session, ADVANCED_DETAIL_POLICIES[id]))
  const detail = permittedOptions.some(([id]) => id === routeDetail) ? routeDetail : permittedOptions[0]?.[0]
  return (
    <div className="advanced-settings-stack">
      <Surface className="advanced-navigation" title="Advanced" subtitle="Technical controls are separated from everyday home settings.">
        <div className="detail-picker">{permittedOptions.map(([id, Icon, label]) => <button type="button" className={detail === id ? 'active' : ''} key={id} onClick={() => { navigate(`/settings/advanced/${id}`); }}><Icon />{label}</button>)}</div>
      </Surface>
      {detail === 'system-health' && <HealthDetail />}
      {detail === 'sensor-test-mode' && <SensorTestModeDetail homeId={home?.id} currency={home?.currency ?? 'USD'} />}
      {detail === 'network' && <NetworkDetail />}
      {detail === 'rates' && home && <AdvancedRateSettings home={home} services={services} />}
      {detail === 'topology' && home && <TopologyDetail homeId={home.id} />}
      {detail === 'firmware' && <FirmwareDetail />}
      {detail === 'interface' && <InterfaceTextDetail />}
      {detail === 'layout' && <StatusLayoutDetail />}
      {detail === 'logs' && <LogsDetail />}
      {detail === 'security' && <SecurityDetail />}
      {detail === 'data-reset' && home && <DataResetWorkflow siteId={home.id} siteName={home.name} mfaEnabled={Boolean(session?.user?.mfaEnabled)} />}
    </div>
  )
}

function HealthDetail() {
  const navigate = useNavigate()
  const health = useQuery({
    queryKey: ['advanced-health', __FRONTEND_VERSION__],
    queryFn: async ({ signal }) => {
      const controller = new AbortController()
      const timeout = window.setTimeout(() => { controller.abort() }, 10_000)
      signal.addEventListener('abort', () => { controller.abort() }, { once: true })
      try {
        return await request(
          '/api/v1/system/health',
          {
            headers: { 'X-Power-Monitor-Frontend-Version': __FRONTEND_VERSION__ },
            signal: controller.signal,
          },
          adaptSystemHealth,
        )
      } finally {
        window.clearTimeout(timeout)
      }
    },
    retry: false,
  })
  if (health.isLoading) return <LoadingState label="Checking API, database, worker, storage, backups, live data, and rates…" />
  if (health.error) return <SystemHealthError error={health.error} retry={() => void health.refetch()} />
  const cssAsset = document.querySelector<HTMLLinkElement>('link[rel="stylesheet"]')?.href.split('/').at(-1) ?? 'development styles'
  const data = health.data
  if (!data) return <SystemHealthError error={new Error('The System Health response was empty.')} retry={() => void health.refetch()} />
  return (
    <div className="health-dashboard">
      <Surface
        className={`health-overall ${data.status}`}
        title="System health"
        subtitle={`Checked ${relativeTime(data.checkedAt)} · specific diagnostics contain no credentials or sensitive paths.`}
        action={<span className={`health-status ${data.status}`}>{healthStateLabel(data.status)}</span>}
      >
        <p>{healthOverallSummary(data.status)}</p>
        <button type="button" className="button secondary compact" onClick={() => void health.refetch()}><RefreshCw /> Check again</button>
      </Surface>
      <div className="health-component-grid">
        {data.components.map((component) => (
          <Surface className={`health-component ${component.status}`} key={component.key}>
            <header>
              <span className={`health-indicator ${component.status}`} aria-hidden="true">
                {component.status === 'healthy' ? <CheckCircle2 /> : component.status === 'unknown' ? <Clock3 /> : <Gauge />}
              </span>
              <div><small>{component.label}</small><strong>{healthStateLabel(component.status)}</strong></div>
            </header>
            <p>{component.summary}</p>
            <dl>
              {component.lastSuccessAt && <><dt>Last success</dt><dd>{relativeTime(component.lastSuccessAt)}</dd></>}
              {component.latencyMs !== undefined && <><dt>Latency</dt><dd>{component.latencyMs.toFixed(1)} ms</dd></>}
            </dl>
            {component.remediation?.route && (
              <button type="button" className="text-button" onClick={() => { navigate(component.remediation?.route ?? '/settings/advanced/system-health'); }}>
                {component.remediation.label}
              </button>
            )}
          </Surface>
        ))}
      </div>
      <Surface title="Release compatibility" subtitle="Frontend, API contract, protocol, and container identity.">
        {data.versions.compatibility === 'mismatch' && (
          <InlineNotice tone="danger">
            Frontend and API versions differ. Update the immutable frontend and API images together before relying on these diagnostics.
          </InlineNotice>
        )}
        <div className="health-version-grid">
          {Object.entries(data.versions).map(([label, value]) => (
            <div key={label}><small>{label.replaceAll('_', ' ')}</small><strong>{value ?? 'Not reported'}</strong></div>
          ))}
          <div><small>frontend commit</small><strong>{__FRONTEND_COMMIT__}</strong></div>
          <div><small>CSS bundle</small><code className="bundle-identity">{cssAsset}</code></div>
        </div>
      </Surface>
      <Surface title="Recent health events" subtitle="Current diagnostic findings, newest first.">
        {data.recentEvents.length ? data.recentEvents.map((event) => (
          <div className="list-row" key={`${event.component}-${event.occurredAt}`}>
            <span><strong>{event.component.replaceAll('_', ' ')}</strong><small>{event.summary}</small></span>
            <span className={`pill ${event.status === 'healthy' ? 'success' : 'warning'}`}>{healthStateLabel(event.status)}</span>
          </div>
        )) : <EmptyState compact title="No current health events" message="All available checks are healthy or not yet applicable." />}
      </Surface>
    </div>
  )
}

function SystemHealthError({ error, retry }: { error: unknown; retry: () => void }) {
  const [showVersions, setShowVersions] = useState(false)
  let title = 'System Health request failed'
  let message = error instanceof Error ? error.message : 'The request could not be completed.'
  if (error instanceof ApiError) {
    if (error.problem.status === 401) {
      title = 'Sign in again'
      message = 'Your session expired before System Health could be checked.'
    } else if (error.problem.status === 403) {
      title = 'Owner access required'
      message = 'System Health contains administrator diagnostics and is available only to a home owner.'
    } else if (error.problem.status === 404) {
      title = 'System Health service is unavailable'
      message = 'The settings page loaded, but the server health endpoint could not be found.'
    } else if (error.problem.status >= 500) {
      title = 'System Health service error'
      message = 'The API received the request but could not complete its diagnostic checks.'
    }
  } else if (error instanceof TypeError && error.message.includes('incompatible')) {
    title = 'Frontend and API versions differ'
    message = 'The running frontend and API use different System Health contracts. Update both immutable images to the same release.'
  } else if (error instanceof DOMException && error.name === 'AbortError') {
    title = 'System Health request timed out'
    message = 'The server did not complete the health check within 10 seconds. Retry, then review the API container if it continues.'
  }
  return (
    <Surface>
      <div className="state-block error-state" role="alert">
        <Gauge aria-hidden="true" />
        <div>
          <strong>{title}</strong>
          <p>{message}</p>
          <div className="inline-actions">
            <button type="button" className="button secondary" onClick={retry}><RefreshCw /> Retry health check</button>
            <button type="button" className="button secondary" onClick={() => { setShowVersions(!showVersions); }}>View versions</button>
          </div>
          {showVersions && (
            <dl className="health-error-versions">
              <dt>Frontend</dt><dd>{__FRONTEND_VERSION__}</dd>
              <dt>Frontend commit</dt><dd>{__FRONTEND_COMMIT__}</dd>
              <dt>API</dt><dd>Unavailable from this request</dd>
            </dl>
          )}
        </div>
      </div>
    </Surface>
  )
}

function healthStateLabel(status: SystemHealthStatus): string {
  return status.slice(0, 1).toUpperCase() + status.slice(1)
}

function healthOverallSummary(status: SystemHealthStatus): string {
  if (status === 'healthy') return 'All applicable core checks are healthy.'
  if (status === 'degraded') return 'The server is available, but one or more components need attention.'
  if (status === 'unhealthy') return 'A core service is unavailable. Use the component guidance below.'
  return 'The server responded, but enough component evidence is not available yet.'
}

function SensorTestModeDetail({ homeId, currency }: { homeId?: string; currency: string }) {
  const testMode = useTestMode()
  if (testMode.loading && !testMode.state) return <LoadingState label="Checking Sensor Test Mode…" />
  return (
    <>
      {testMode.state?.endReason === 'expired' && (
        <InlineNotice tone="warning">
          Sensor Test Mode expired {testMode.state.endedAt ? relativeTime(testMode.state.endedAt) : ''}. All synthetic sensors and session history were discarded; real readings were unchanged.
        </InlineNotice>
      )}
      <SensorTestModeControls key={testMode.state?.sessionId ?? 'disabled'} homeId={homeId} currency={currency} />
    </>
  )
}

function SensorTestModeControls({ homeId, currency }: { homeId?: string; currency: string }) {
  const testMode = useTestMode()
  const [sensorCount, setSensorCount] = useState(testMode.state?.sensorCount ?? 1)
  const [loadProfile, setLoadProfile] = useState<TestLoadProfile>(testMode.state?.loadProfile ?? 'variable_household')
  const [offlineIndexes, setOfflineIndexes] = useState('')
  const [customLoadW, setCustomLoadW] = useState(String(testMode.state?.customLoadW ?? 1800))
  const [baseLoadW, setBaseLoadW] = useState(String(testMode.state?.baseLoadW ?? 1000))
  const [variationPercent, setVariationPercent] = useState(String(testMode.state?.variationPercent ?? 20))
  const [sampleInterval, setSampleInterval] = useState(testMode.state?.sampleIntervalSeconds ?? 5)
  const [expiryMinutes, setExpiryMinutes] = useState<number | null>(
    testMode.state?.expiresAt && testMode.state.startedAt
      ? Math.max(5, Math.round((new Date(testMode.state.expiresAt).getTime() - new Date(testMode.state.startedAt).getTime()) / 60_000))
      : testMode.state?.enabled
        ? null
        : 15,
  )
  const [costPreview, setCostPreview] = useState(testMode.state?.costPreviewEnabled ?? false)
  const sensors = useQuery({
    queryKey: ['sensor-test-mode-sensors'],
    queryFn: () => request('/api/v1/test-mode/sensors', {}, adaptTestModeSensors),
    enabled: Boolean(testMode.state?.enabled),
    refetchInterval: testMode.state?.enabled ? 5_000 : false,
  })
  const history = useQuery({
    queryKey: ['sensor-test-mode-history'],
    queryFn: () => request('/api/v1/test-mode/history?limit=96', {}, adaptTestModeHistory),
    enabled: Boolean(testMode.state?.enabled),
    refetchInterval: testMode.state?.enabled ? 5_000 : false,
  })
  const indexes = offlineIndexes
    .split(',')
    .map((value) => Number(value.trim()))
    .filter((value) => Number.isInteger(value) && value >= 1 && value <= sensorCount)
  const configuration = {
    sensorCount,
    loadProfile,
    offlineSensorIndexes: [...new Set(indexes)],
    customLoadW: loadProfile === 'custom' ? Number(customLoadW) : undefined,
    baseLoadW: Number(baseLoadW),
    variationPercent: Number(variationPercent),
    sampleIntervalSeconds: sampleInterval,
    expiresInMinutes: expiryMinutes,
    costPreviewEnabled: costPreview,
    paused: testMode.state?.paused ?? false,
    siteId: homeId,
  }
  const save = async () => {
    if (testMode.state?.enabled) await testMode.update(configuration)
    else await testMode.enable(configuration)
  }
  return (
    <div className="test-mode-settings">
      <Surface
        className={testMode.state?.enabled ? 'test-mode-surface active' : 'test-mode-surface'}
        title="Sensor Test Mode"
        subtitle="Owner-only, temporary synthetic readings for verifying dashboard behavior without an ESP32."
        action={<span className={`pill ${testMode.state?.enabled ? 'warning' : ''}`}>{testMode.state?.enabled ? 'Test Mode active' : 'Off'}</span>}
      >
        <InlineNotice tone="warning">
          <FlaskConical />
          Synthetic data stays in process memory and is excluded from real readings, bills, finalized costs, exports, backups, alerts, device credentials, and firmware.
        </InlineNotice>
        <form className="test-mode-form" onSubmit={(event) => { event.preventDefault(); void save() }}>
          <div className="form-grid">
            <label>Simulated active sensors<input type="number" min={0} max={32} step={1} value={sensorCount} onChange={(event) => { setSensorCount(Number(event.target.value)); }} /></label>
            <label>Load profile<select value={loadProfile} onChange={(event) => { setLoadProfile(event.target.value as TestLoadProfile); }}><option value="steady">Steady</option><option value="variable_household">Variable household</option><option value="morning_evening_peaks">Morning/evening peaks</option><option value="high_load">High load</option><option value="low_load">Low load</option><option value="solar_day">Daytime solar offset</option><option value="custom">Custom</option></select></label>
            {loadProfile === 'custom' && <label>Whole-home load (W)<input type="number" min={0} max={250000} value={customLoadW} onChange={(event) => { setCustomLoadW(event.target.value); }} /></label>}
            <label>Base load (W)<input type="number" min={0} max={250000} value={baseLoadW} onChange={(event) => { setBaseLoadW(event.target.value); }} /></label>
            <label>Variation (%)<input type="number" min={0} max={100} value={variationPercent} onChange={(event) => { setVariationPercent(event.target.value); }} /></label>
            <label>Simulate offline sensors<input value={offlineIndexes} onChange={(event) => { setOfflineIndexes(event.target.value); }} placeholder="Indexes, for example 2, 4" /></label>
            <label>Sample interval<select value={sampleInterval} onChange={(event) => { setSampleInterval(Number(event.target.value)); }}><option value={1}>1 second</option><option value={5}>5 seconds</option><option value={15}>15 seconds</option><option value={30}>30 seconds</option><option value={60}>60 seconds</option></select></label>
            <label>Automatic expiry<select value={expiryMinutes ?? 'until-disabled'} onChange={(event) => { setExpiryMinutes(event.target.value === 'until-disabled' ? null : Number(event.target.value)); }}><option value={15}>15 minutes</option><option value={60}>1 hour</option><option value={240}>4 hours</option><option value="until-disabled">Until disabled</option></select></label>
          </div>
          <label className="toggle-row test-cost-opt-in">
            <span><strong>Temporary current-rate cost preview</strong><small>Uses the current reviewed energy rate in memory only. It never creates a bill, cost row, or export.</small></span>
            <input type="checkbox" checked={costPreview} onChange={(event) => { setCostPreview(event.target.checked); }} />
          </label>
          <div className="form-actions">
            <button type="submit" className="button primary" disabled={testMode.changing}>{testMode.state?.enabled ? 'Apply test settings' : 'Enable Sensor Test Mode'}</button>
            {testMode.state?.enabled && <button type="button" className="button secondary" disabled={testMode.changing} onClick={() => { void testMode.update({ paused: !testMode.state?.paused }) }}>{testMode.state.paused ? 'Resume simulation' : 'Pause simulation'}</button>}
            {testMode.state?.enabled && <button type="button" className="button secondary" disabled={testMode.changing} onClick={() => { void testMode.reset() }}>Reset synthetic history</button>}
            {testMode.state?.enabled && <button type="button" className="button danger" disabled={testMode.changing} onClick={() => { if (confirm('Exit Sensor Test Mode and permanently discard all synthetic readings? Real data is not changed.')) void testMode.disable() }}>Exit and clean up</button>}
          </div>
          {Boolean(testMode.error) && <InlineNotice tone="danger">{testMode.error instanceof Error ? testMode.error.message : 'Sensor Test Mode could not be changed.'}</InlineNotice>}
        </form>
      </Surface>
      {testMode.state?.enabled && (
        <>
          <Surface title="Live test session" subtitle={`${testMode.state.paused ? 'Paused' : testMode.state.expiresAt ? `Expires ${relativeTime(testMode.state.expiresAt)}` : 'Runs until disabled'} · source_type=simulated · environment=test_mode`}>
            <div className="test-mode-summary">
              <div><small>Simulated load</small><strong>{power(testMode.state.currentPowerW)}</strong></div>
              <div><small>Test energy</small><strong>{energy(testMode.state.totalEnergyKwh)}</strong></div>
              <div><small>Simulated sensors</small><strong>{testMode.state.onlineSensors}/{testMode.state.sensorCount}</strong></div>
              <div><small>Temporary preview</small><strong>{testMode.state.costPreview?.available ? money(testMode.state.costPreview.estimatedEnergyCost, testMode.state.costPreview.currency ?? currency) : 'Unavailable'}</strong></div>
            </div>
            {testMode.state.costPreview && <InlineNotice>{testMode.state.costPreview.disclosure}</InlineNotice>}
          </Surface>
          <Surface title="Simulated sensor controls" subtitle="Stable identities last only for this test session.">
            {sensors.isLoading ? <LoadingState /> : sensors.error ? <ErrorState error={sensors.error} retry={() => void sensors.refetch()} /> : sensors.data?.map((sensor) => (
              <div className="list-row" key={sensor.id}>
                <FlaskConical />
                <span><strong>{sensor.name}</strong><small>{power(sensor.currentPowerW)} · {energy(sensor.energyKwh)}</small></span>
                <span className={`pill ${sensor.online ? 'success' : 'warning'}`}>{sensor.online ? 'Test online' : 'Test offline'}</span>
              </div>
            ))}
          </Surface>
          <Surface title="Recent synthetic samples" subtitle="Displayed only inside Test Mode and discarded on exit or expiry.">
            {history.data?.length ? <div className="test-history-list">{history.data.slice(-12).reverse().map((point) => <div key={`${point.sensorId}-${point.recordedAt}`}><span>{point.sensorName}</span><strong>{point.online ? power(point.powerW) : 'Offline'}</strong><small>{relativeTime(point.recordedAt)}</small></div>)}</div> : <EmptyState compact title="Waiting for the first sample" message="The simulator records the first isolated point within the configured interval." />}
          </Surface>
        </>
      )}
    </div>
  )
}

function NetworkDetail() {
  const runtime = useQuery({ queryKey: ['network-runtime'], queryFn: () => request<Record<string, unknown>>('/api/v1/admin/network/runtime') })
  return <Surface title="Sensor network policy" subtitle="Signed device authentication remains required in every mode.">{runtime.isLoading ? <LoadingState /> : runtime.error ? <ErrorState error={runtime.error} /> : <pre className="structured-data">{JSON.stringify(runtime.data, null, 2)}</pre>}</Surface>
}

function TopologyDetail({ homeId }: { homeId: string }) {
  const navigate = useNavigate()
  const { sensors } = useLiveHome()
  const circuits = useQuery({ queryKey: ['circuits', homeId], queryFn: () => request(`/api/v1/circuits?site_id=${encodeURIComponent(homeId)}`, {}, adaptCircuits) })
  const aggregates = useQuery({ queryKey: ['aggregates', homeId], queryFn: () => request<Record<string, unknown>[]>(`/api/v1/aggregate-sets?site_id=${encodeURIComponent(homeId)}`) })
  const incomplete = sensors.filter((sensor) => !sensor.circuitId || !sensor.utilityAccountId)
  return <Surface title="Monitoring topology" subtitle="Whole-home totals and partial circuits remain server-authoritative and double-count protected." action={<button type="button" className="button secondary compact" onClick={() => { navigate('/settings/sensors?configuration=measurement-assignment') }}>Manage assignments</button>}>{circuits.isLoading ? <LoadingState /> : <><div className="list-row"><Radio /><span><strong>{circuits.data?.length ?? 0} monitored circuits</strong><small>{aggregates.data?.length ?? 0} aggregate sets · {incomplete.length} sensors need assignment</small></span></div>{circuits.data?.map((item) => <div className="list-row" key={item.id}><span><strong>{item.name}</strong><small>{item.measurementRole.replaceAll('-', ' ')}</small></span></div>)}{aggregates.data?.map((item, index) => <div className="list-row" key={typeof item.id === 'string' ? item.id : String(index)}><span><strong>{typeof item.name === 'string' ? item.name : 'Monitoring group'}</strong><small>{typeof item.cost_scope === 'string' ? item.cost_scope : 'server managed'}</small></span></div>)}</>}</Surface>
}

function FirmwareDetail() {
  return (
    <Surface title="Firmware" subtitle="Verified ESP32 application images use the enrolled sensor credential and the existing trusted HTTPS connection.">
      <FirmwareFleetWorkflow />
    </Surface>
  )
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
  const [permissionSaved, setPermissionSaved] = useState(false)
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
        {permissionSaved && <InlineNotice tone="success">Permissions saved successfully.</InlineNotice>}
      </Surface>
      <Surface title="Audit log" subtitle="Recent access, configuration, and security events.">
        {audit.data?.map((event, index) => <div className="list-row" key={text(event.id, String(index))}><Shield /><span><strong>{text(event.action, 'Audit event')}</strong><small>{text(event.occurred_at)}</small></span></div>) ?? <LoadingState />}
      </Surface>
      {(editingRole || cloneSource) && permissions.data && <RoleEditor
        source={editingRole === 'new' ? undefined : editingRole ?? cloneSource}
        mode={editingRole === 'new' ? 'create' : editingRole ? 'edit' : 'clone'}
        permissions={permissions.data}
        mfaEnabled={Boolean(session?.user?.mfaEnabled)}
        onClose={() => { setEditingRole(undefined); setCloneSource(undefined); }}
        onSaved={() => { setPermissionSaved(true); void client.invalidateQueries({ queryKey: ['family-roles'] }); setEditingRole(undefined); setCloneSource(undefined); }}
      />}
    </>
  )
}

export function RoleEditor({
  source,
  mode,
  permissions,
  mfaEnabled,
  onClose,
  onSaved,
}: {
  source?: FamilyRoleOption
  mode: 'create' | 'edit' | 'clone'
  permissions: PermissionOption[]
  mfaEnabled: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(mode === 'clone' ? `${source?.name ?? 'Role'} copy` : source?.name ?? '')
  const [description, setDescription] = useState(source?.description ?? '')
  const [selected, setSelected] = useState<string[]>(source?.permissions ?? [])
  const [reason, setReason] = useState(mode === 'edit' ? 'Custom role revised' : 'Custom role created')
  const [confirmingProtectedChange, setConfirmingProtectedChange] = useState(false)
  const groups = permissions.reduce<Record<string, PermissionOption[]>>((result, permission) => {
    result[permission.group] ??= []
    result[permission.group]?.push(permission)
    return result
  }, {})
  const highRiskCodes = new Set(permissions.filter((permission) => permission.highRisk).map((permission) => permission.code))
  const changedPermissions = mode === 'edit'
    ? new Set([...selected.filter((code) => !source?.permissions.includes(code)), ...(source?.permissions ?? []).filter((code) => !selected.includes(code))])
    : new Set(selected)
  const protectedChange = [...changedPermissions].some((code) => highRiskCodes.has(code))
  const save = useMutation({
    mutationFn: () => {
      const payload = {
        display_name: name,
        description,
        permissions: selected,
        expected_revision: mode === 'edit' ? source?.revision : undefined,
        reason,
        confirm_high_risk: protectedChange,
      }
      const path = mode === 'edit'
        ? `/api/v1/admin/roles/${source?.id ?? ''}`
        : mode === 'clone'
          ? `/api/v1/admin/roles/${source?.id ?? ''}/clone`
          : '/api/v1/admin/roles'
      return request(path, json(mode === 'edit' ? 'PUT' : 'POST', payload))
    },
    onSuccess: onSaved,
    onError: (error) => {
      if (error instanceof ApiError && error.problem.code === 'reauthentication_required') setConfirmingProtectedChange(true)
    },
  })
  const protectedError = save.error instanceof ApiError && save.error.problem.code === 'reauthentication_required'
  return (
    <>
    {!confirmingProtectedChange && <div className="modal-backdrop">
      <form className="modal-card role-editor" role="dialog" aria-modal="true" aria-labelledby="role-editor-title" onSubmit={(event) => { event.preventDefault(); if (protectedChange) setConfirmingProtectedChange(true); else save.mutate() }}>
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
          {save.error && !protectedError && <InlineNotice tone="danger">{save.error.message}</InlineNotice>}
        </div>
        <footer><button className="button secondary" type="button" onClick={onClose}>Cancel</button><button className="button primary" type="submit" disabled={save.isPending || selected.length === 0}>{save.isPending ? 'Saving…' : 'Save role'}</button></footer>
      </form>
    </div>}
    {confirmingProtectedChange && <ProtectedChangeDialog mfaEnabled={mfaEnabled} onCancel={() => { setConfirmingProtectedChange(false) }} onConfirmed={() => { setConfirmingProtectedChange(false); save.mutate() }} />}
    </>
  )
}
