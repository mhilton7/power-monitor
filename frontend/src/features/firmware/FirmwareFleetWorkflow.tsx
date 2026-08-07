import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, FileUp, RefreshCw, ShieldCheck, TriangleAlert, X } from 'lucide-react'
import { useState } from 'react'
import { hasPermission } from '../../access/permissions'
import {
  adaptFirmwareDeployments,
  adaptFirmwareRelease,
  adaptFirmwareReleases,
} from '../../api/adapters'
import { errorMessage, json, request } from '../../api/client'
import { EmptyState, ErrorState, InlineNotice, LoadingState } from '../../components/feedback/States'
import { useAuth } from '../../state/AuthContext'
import { useLiveHome } from '../../state/LiveHomeContext'
import type { FirmwareDeploymentSummary, FirmwareReleaseSummary } from '../../types/models'
import { fileSize, statusLabel } from '../../utils/format'
import {
  FirmwareDeploymentStatus,
  firmwareCancellableStates,
  firmwareDeploymentLabel,
  firmwareRetryableStates,
  firmwareTerminalStates,
} from './FirmwareDeploymentStatus'

function adaptCreated(value: unknown): FirmwareDeploymentSummary[] {
  if (Array.isArray(value)) return adaptFirmwareDeployments(value)
  if (value && typeof value === 'object') return adaptFirmwareDeployments((value as Record<string, unknown>).deployments)
  throw new TypeError('firmware rollout returned an invalid response')
}

function deploymentFromAction(value: unknown): FirmwareDeploymentSummary | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const source = value as Record<string, unknown>
  const candidate = source.deployment && typeof source.deployment === 'object'
    ? source.deployment
    : source.id ? source : undefined
  return candidate ? adaptFirmwareDeployments([candidate])[0] : undefined
}

export function FirmwareFleetWorkflow() {
  const client = useQueryClient()
  const { session } = useAuth()
  const { sensors } = useLiveHome()
  const canManage = hasPermission(session, 'firmware.manage')
  const canDeploy = hasPermission(session, 'firmware.deploy')
  const [file, setFile] = useState<File>()
  const [release, setRelease] = useState<FirmwareReleaseSummary>()
  const [selected, setSelected] = useState<string[]>([])

  const releases = useQuery({ queryKey: ['firmware-releases'], queryFn: () => request('/api/v1/firmware-releases', {}, adaptFirmwareReleases) })
  const deployments = useQuery({
    queryKey: ['firmware-deployments'],
    queryFn: () => request('/api/v1/firmware-deployments', {}, adaptFirmwareDeployments),
    refetchInterval: (query) => query.state.data?.some((item) => !firmwareTerminalStates.has(item.state)) ? 5_000 : false,
  })

  const upload = useMutation({
    mutationFn: () => {
      if (!file || file.name.toLowerCase() !== 'firmware.bin') throw new Error('Choose the application file named firmware.bin.')
      const form = new FormData()
      form.append('binary', file, file.name)
      return request('/api/v1/firmware-releases', { method: 'POST', body: form }, adaptFirmwareRelease)
    },
    onSuccess: (value) => {
      setRelease(value)
      // Target selection is deliberately explicit. Verifying a file must never
      // silently opt every eligible sensor into a rollout.
      setSelected([])
      void client.invalidateQueries({ queryKey: ['firmware-releases'] })
    },
  })

  const deploy = useMutation({
    mutationFn: () => {
      if (!release || selected.length === 0) throw new Error('Select at least one compatible sensor.')
      return request('/api/v1/firmware-deployments', json('POST', {
        firmware_release_id: release.id,
        device_ids: selected,
        scheduled_at: new Date().toISOString(),
        canary_first: true,
        maximum_concurrency: 1,
      }), adaptCreated)
    },
    onSuccess: () => {
      setFile(undefined)
      setRelease(undefined)
      setSelected([])
      void client.invalidateQueries({ queryKey: ['firmware-deployments'] })
    },
  })

  const action = useMutation({
    mutationFn: ({ id, verb }: { id: string; verb: 'cancel' | 'retry' | 'promote' }) => request<unknown>(`/api/v1/firmware-deployments/${encodeURIComponent(id)}/${verb}`, json('POST')),
    onSuccess: (value) => {
      const updated = deploymentFromAction(value)
      if (updated) {
        client.setQueryData<FirmwareDeploymentSummary[]>(['firmware-deployments'], (current) => [
          updated,
          ...(current ?? []).filter((item) => item.id !== updated.id),
        ])
      }
      void client.invalidateQueries({ queryKey: ['firmware-deployments'] })
    },
  })

  const error = upload.error ?? deploy.error ?? action.error
  const toggle = (id: string) => {
    setSelected((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id])
  }

  return (
    <div className="firmware-fleet-workflow">
      <InlineNotice tone="success"><ShieldCheck /> Existing trusted HTTPS and each enrolled sensor credential authenticate every deployment. No additional certificate, signing key, manifest, or hash entry is required.</InlineNotice>

      {canManage && (
        <section className="firmware-fleet-upload" aria-labelledby="firmware-fleet-upload-title">
          <div><h3 id="firmware-fleet-upload-title">Prepare a multi-sensor release</h3><p>Upload one firmware.bin. The server derives and verifies all release metadata before any sensor can be selected.</p></div>
          {!release ? <>
            <label className="firmware-file-picker compact">
              <FileUp />
              <span><strong>Choose firmware.bin</strong><small>ESP32-S3 application image only</small></span>
              <input type="file" accept=".bin,application/octet-stream" onChange={(event) => { setFile(event.target.files?.[0]); upload.reset() }} />
              {file && <span className="pill">{file.name} · {fileSize(file.size)}</span>}
            </label>
            <button className="button primary" type="button" disabled={!file || upload.isPending} onClick={() => { upload.mutate() }}>{upload.isPending ? 'Server verification…' : 'Verify firmware'}</button>
          </> : <>
            <div className="firmware-fleet-release">
              <Check />
              <span><strong>{release.version} verified for {release.hardwareTarget}</strong><small>{release.projectName} · {fileSize(release.sizeBytes)} · SHA-256 {release.sha256}</small></span>
              <button className="icon-button" type="button" aria-label="Discard prepared release selection" onClick={() => { setRelease(undefined); setFile(undefined); setSelected([]) }}><X /></button>
            </div>
            {canDeploy && <fieldset className="firmware-targets">
              <legend>Select sensors</legend>
              {sensors.map((sensor) => {
                const ready = sensor.firmwareOta?.state === 'ready' && sensor.firmware !== release.version
                const canary = selected[0] === sensor.id
                return <label key={sensor.id} className={!ready ? 'disabled' : ''}>
                  <input type="checkbox" checked={selected.includes(sensor.id)} disabled={!ready} onChange={() => { toggle(sensor.id) }} />
                  <span>
                    <strong>{sensor.name}{canary && <span className="pill success firmware-canary-label">Canary</span>}</strong>
                    <small>{ready ? `${sensor.firmware ?? 'Unknown'} → ${release.version}` : sensor.firmware === release.version ? 'Already current' : sensor.firmwareOta?.state.replaceAll('_', ' ') ?? 'OTA unsupported'}</small>
                  </span>
                </label>
              })}
              <div className="firmware-rollout-policy"><span><strong>Rollout policy</strong><small>Canary first · maximum concurrency 1</small></span><span className="pill success">Safe default</span></div>
              <p>The first selected sensor is the canary. Additional sensors remain waiting until the server confirms the required healthy heartbeats, one reading batch, no rollback, and no critical alert.</p>
              <button className="button primary" type="button" disabled={selected.length === 0 || deploy.isPending} onClick={() => { deploy.mutate() }}>{deploy.isPending ? 'Scheduling…' : `Install on ${selected.length} sensor${selected.length === 1 ? '' : 's'}`}</button>
            </fieldset>}
          </>}
        </section>
      )}

      {error && <InlineNotice tone="danger"><TriangleAlert /> {errorMessage(error)}</InlineNotice>}

      <section aria-labelledby="firmware-releases-title">
        <h3 id="firmware-releases-title">Verified releases</h3>
        {releases.isLoading ? <LoadingState /> : releases.error ? <ErrorState error={releases.error} retry={() => void releases.refetch()} /> : releases.data?.length ? releases.data.map((item) => (
          <div className="list-row" key={item.id}>
            <RefreshCw />
            <span><strong>{item.version}</strong><small>{item.projectName} · {item.hardwareTarget} · {fileSize(item.sizeBytes)} · {item.trustMode === 'existing_device_hmac' ? 'existing device HMAC' : 'legacy Ed25519'}</small></span>
            <span className={`pill ${item.verificationStatus === 'verified' ? 'success' : 'warning'}`}>{statusLabel(item.verificationStatus)}</span>
          </div>
        )) : <EmptyState title="No firmware releases" message="Choose Update firmware from one sensor, or prepare a multi-sensor release above." />}
      </section>

      <section aria-labelledby="firmware-deployments-title">
        <h3 id="firmware-deployments-title">Recent deployments</h3>
        {deployments.isLoading ? <LoadingState /> : deployments.error ? <ErrorState error={deployments.error} retry={() => void deployments.refetch()} /> : deployments.data?.length ? deployments.data.map((item) => (
          <article className="firmware-deployment-card" key={item.id}>
            <div className="list-row firmware-deployment-row">
              <RefreshCw className={firmwareTerminalStates.has(item.state) ? '' : 'spin'} />
              <span>
                <strong>{firmwareDeploymentLabel(item)}</strong>
                <small>
                  {sensors.find((sensor) => sensor.id === item.deviceId)?.name ?? item.deviceId}
                  {' · '}attempt {item.attempt}
                  {' · '}{item.progressMode === 'determinate'
                    ? `${item.progress}% · ${fileSize(item.bytesReceived)}`
                    : firmwareTerminalStates.has(item.state) ? 'Terminal outcome recorded' : 'Waiting for authenticated progress'}
                  {item.rolloutOrder !== undefined ? ` · rollout ${item.rolloutOrder + 1}` : ''}
                </small>
              </span>
              <span className={`pill ${item.state === 'completed' ? 'success' : item.state === 'failed' || item.state === 'rolled_back' ? 'danger' : ''}`}>{item.targetVersion ?? 'Pending'}</span>
              {canDeploy && <div className="inline-actions">
                {firmwareCancellableStates.has(item.state) && <button className="button secondary compact" type="button" disabled={action.isPending} onClick={() => { action.mutate({ id: item.id, verb: 'cancel' }) }}>Cancel</button>}
                {firmwareRetryableStates.has(item.state) && <button className="button secondary compact" type="button" disabled={action.isPending} onClick={() => { action.mutate({ id: item.id, verb: 'retry' }) }}>Retry</button>}
                {item.state === 'completed' && item.rolloutGroupId && <button className="button primary compact" type="button" disabled={action.isPending || !item.readingConfirmedAt || (item.verification
                  ? item.verification.verificationHeartbeatRequired === undefined
                    || item.verification.verificationHeartbeatCount < item.verification.verificationHeartbeatRequired
                    || item.verification.checks.some((check) => check.status !== 'passed')
                  : item.verificationHeartbeats < 10)} onClick={() => { action.mutate({ id: item.id, verb: 'promote' }) }}>Promote next</button>}
              </div>}
            </div>
            <FirmwareDeploymentStatus deployment={item} compact />
          </article>
        )) : <p>No deployment records.</p>}
      </section>
    </div>
  )
}
