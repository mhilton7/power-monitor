import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, FileUp, ShieldCheck, TriangleAlert, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import {
  adaptFirmwareDeployments,
  adaptFirmwareReadiness,
  adaptFirmwareRelease,
} from '../../api/adapters'
import { errorMessage, json, request } from '../../api/client'
import { InlineNotice } from '../../components/feedback/States'
import type { FirmwareDeploymentSummary, FirmwareReleaseSummary, SensorSummary } from '../../types/models'
import {
  FirmwareDeploymentStatus,
  firmwareCancellableStates,
  firmwareRetryableStates,
  firmwareTerminalStates,
} from './FirmwareDeploymentStatus'

function bytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  if (value < 1024) return `${Math.round(value)} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`
  return `${(value / 1024 ** 2).toFixed(1)} MiB`
}

function readinessLabel(state: SensorSummary['firmwareOta']): string {
  switch (state?.state) {
    case 'ready': return 'Ready for server OTA'
    case 'legacy_signed_ota_only': return 'Legacy signed OTA only'
    case 'trust_missing': return 'OTA trust missing'
    case 'bootstrap_required': return 'One-time bootstrap required'
    default: return 'OTA unsupported'
  }
}

function adaptCreatedDeployments(value: unknown): FirmwareDeploymentSummary[] {
  if (Array.isArray(value)) return adaptFirmwareDeployments(value)
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return adaptFirmwareDeployments((value as Record<string, unknown>).deployments)
  }
  throw new TypeError('firmware deployment creation returned an invalid response')
}

function mergeDeployments(
  current: FirmwareDeploymentSummary[] | undefined,
  updated: FirmwareDeploymentSummary[],
): FirmwareDeploymentSummary[] {
  const updatedIds = new Set(updated.map((item) => item.id))
  return [...updated, ...(current ?? []).filter((item) => !updatedIds.has(item.id))]
}

export function FirmwareUpdateDialog({
  sensor,
  onClose,
}: {
  sensor: SensorSummary
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [file, setFile] = useState<File>()
  const [release, setRelease] = useState<FirmwareReleaseSummary>()
  const [deploymentId, setDeploymentId] = useState<string>()
  const [ignorePreviousDeployment, setIgnorePreviousDeployment] = useState(false)
  const [confirmDowngrade, setConfirmDowngrade] = useState(false)

  const deployments = useQuery({
    queryKey: ['firmware-deployments', sensor.id],
    queryFn: () => request(`/api/v1/firmware-deployments?device_id=${encodeURIComponent(sensor.id)}`, {}, adaptFirmwareDeployments),
    refetchInterval: (query) => {
      const values = query.state.data
      return values?.some((item) => item.deviceId === sensor.id && !firmwareTerminalStates.has(item.state)) ? 2_500 : false
    },
  })
  const deployment = useMemo(() => {
    const matching = deployments.data?.filter((item) => item.deviceId === sensor.id) ?? []
    return matching.find((item) => item.id === deploymentId)
      ?? matching.find((item) => !firmwareTerminalStates.has(item.state))
      ?? (ignorePreviousDeployment ? undefined : matching[0])
  }, [deploymentId, deployments.data, ignorePreviousDeployment, sensor.id])

  const readiness = useQuery({
    queryKey: ['firmware-readiness', sensor.id, release?.id],
    queryFn: () => request(
      `/api/v1/devices/${encodeURIComponent(sensor.id)}/firmware-readiness?release_id=${encodeURIComponent(release?.id ?? '')}`,
      {},
      adaptFirmwareReadiness,
    ),
    enabled: Boolean(release),
  })

  const upload = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error('Choose firmware.bin first.')
      if (file.name.toLowerCase() !== 'firmware.bin') throw new Error('Choose the application file named firmware.bin.')
      const form = new FormData()
      form.append('binary', file, file.name)
      return request('/api/v1/firmware-releases', { method: 'POST', body: form }, adaptFirmwareRelease)
    },
    onSuccess: (verifiedRelease) => {
      setRelease(verifiedRelease)
      void queryClient.invalidateQueries({ queryKey: ['firmware-releases'] })
    },
  })

  const install = useMutation({
    mutationFn: () => {
      if (!release) throw new Error('Verify a firmware file first.')
      return request('/api/v1/firmware-deployments', json('POST', {
        firmware_release_id: release.id,
        device_ids: [sensor.id],
        scheduled_at: new Date().toISOString(),
        allow_downgrade: confirmDowngrade,
        canary_first: true,
        maximum_concurrency: 1,
      }), adaptCreatedDeployments)
    },
    onSuccess: (created) => {
      const first = created[0]
      if (first) setDeploymentId(first.id)
      queryClient.setQueryData<FirmwareDeploymentSummary[]>(
        ['firmware-deployments', sensor.id],
        (current) => mergeDeployments(current, created),
      )
      void queryClient.invalidateQueries({ queryKey: ['firmware-deployments', sensor.id] })
    },
  })

  const cancel = useMutation({
    mutationFn: (id: string) => request(`/api/v1/firmware-deployments/${encodeURIComponent(id)}/cancel`, json('POST'), (value) => adaptFirmwareDeployments([value])[0] as FirmwareDeploymentSummary),
    onSuccess: (updated) => {
      setDeploymentId(updated.id)
      queryClient.setQueryData<FirmwareDeploymentSummary[]>(
        ['firmware-deployments', sensor.id],
        (current) => mergeDeployments(current, [updated]),
      )
      void queryClient.invalidateQueries({ queryKey: ['firmware-deployments', sensor.id] })
    },
  })

  const retry = useMutation({
    mutationFn: (id: string) => request(`/api/v1/firmware-deployments/${encodeURIComponent(id)}/retry`, json('POST'), (value) => adaptFirmwareDeployments([value])[0] as FirmwareDeploymentSummary),
    onSuccess: (updated) => {
      setDeploymentId(updated.id)
      queryClient.setQueryData<FirmwareDeploymentSummary[]>(
        ['firmware-deployments', sensor.id],
        (current) => mergeDeployments(current, [updated]),
      )
      void queryClient.invalidateQueries({ queryKey: ['firmware-deployments', sensor.id] })
    },
  })

  const ota = readiness.data?.firmwareOta ?? sensor.firmwareOta
  const bootstrap = readiness.data?.bootstrap
  const bootstrapRequired = Boolean(release && bootstrap?.required)
  const compatible = readiness.data?.compatible === true && ota?.state === 'ready'
  const downgradeRequired = ota?.state === 'ready'
    && readiness.data?.compatibilityReasons.length === 1
    && readiness.data.compatibilityReasons[0] === 'downgrade_requires_confirmation'
  const installReady = compatible || (downgradeRequired && confirmDowngrade)
  const busy = upload.isPending || install.isPending || cancel.isPending || retry.isPending
  const errors = upload.error ?? install.error ?? cancel.error ?? retry.error ?? readiness.error ?? deployments.error

  const startAnother = () => {
    setIgnorePreviousDeployment(true)
    setDeploymentId(undefined)
    setRelease(undefined)
    setFile(undefined)
    setConfirmDowngrade(false)
    upload.reset()
    install.reset()
    cancel.reset()
    retry.reset()
  }

  return (
    <section className="modal-card firmware-update-dialog" role="dialog" aria-modal="true" aria-labelledby="firmware-update-title">
      <header>
        <div><small>Existing-trust firmware update</small><h2 id="firmware-update-title">Update {sensor.name}</h2></div>
        <button className="icon-button" type="button" aria-label="Close firmware update" disabled={busy} onClick={onClose}><X /></button>
      </header>
      <div className="firmware-update-body">
        <div className="firmware-device-summary">
          <span><strong>Sensor</strong><small>{sensor.name}</small></span>
          <span><strong>Current firmware</strong><small>{sensor.firmware ?? 'Unknown'}</small></span>
          <span><strong>Readiness</strong><small>{readinessLabel(ota)}</small></span>
        </div>

        {!release && !deployment && sensor.firmwareOta?.state === 'bootstrap_required' && (
          <InlineNotice tone="warning">
            This sensor needs one non-erasing USB bootstrap before it can use server-managed OTA. Choose the target firmware first so the server can verify it and generate the exact artifact, SHA-256, and command.
          </InlineNotice>
        )}

        {!deployment && !release && (
          <label className="firmware-file-picker">
            <FileUp />
            <span><strong>Choose firmware.bin</strong><small>The server reads and verifies version, target, checksum, project identity, and partition fit automatically.</small></span>
            <input type="file" accept=".bin,application/octet-stream" onChange={(event) => { setFile(event.target.files?.[0]); setConfirmDowngrade(false); upload.reset() }} />
            {file && <span className="pill">{file.name} · {bytes(file.size)}</span>}
          </label>
        )}

        {release && (
          <div className="firmware-verification">
            <div className="firmware-verified-title"><ShieldCheck /><span><strong>Firmware verified</strong><small>Metadata came from the ESP32 application image, not from this browser.</small></span></div>
            <dl>
              <div><dt>Version</dt><dd>{release.version}</dd></div>
              <div><dt>Target</dt><dd>{release.hardwareTarget}</dd></div>
              <div><dt>Project</dt><dd>{release.projectName}</dd></div>
              <div><dt>Size</dt><dd>{bytes(release.sizeBytes)}</dd></div>
              <div><dt>Authentication</dt><dd>Existing device HMAC</dd></div>
              <div><dt>Transport</dt><dd>Existing trusted HTTPS</dd></div>
              <div><dt>Compatibility</dt><dd>{readiness.isLoading ? 'Checking…' : compatible ? 'Ready' : readinessLabel(ota)}</dd></div>
              <div className="wide"><dt>SHA-256</dt><dd>{release.sha256}</dd></div>
            </dl>
            {downgradeRequired && (
              <InlineNotice tone="warning">
                <label className="firmware-downgrade-confirmation">
                  <input
                    type="checkbox"
                    checked={confirmDowngrade}
                    onChange={(event) => { setConfirmDowngrade(event.target.checked) }}
                  />
                  <span><strong>Confirm intentional downgrade</strong><small>This installs an older semantic version. Administrator reauthentication is required, and the sensor independently verifies the signed downgrade permission.</small></span>
                </label>
              </InlineNotice>
            )}
          </div>
        )}

        {bootstrapRequired && bootstrap && (
          <div className="firmware-bootstrap">
            <TriangleAlert />
            <div>
              <h3>One-time OTA bootstrap required</h3>
              <p>Install this verified application image once through USB. Do not erase flash. Enrollment, Wi-Fi, NVS settings, the trusted CA, microSD history, and sequence state remain intact.</p>
              {bootstrap.expectedVersion && <p><strong>Expected version:</strong> {bootstrap.expectedVersion}</p>}
              {bootstrap.sha256 && <p className="breakable"><strong>SHA-256:</strong> {bootstrap.sha256}</p>}
              {bootstrap.usbCommand && <pre>{bootstrap.usbCommand}</pre>}
              {bootstrap.artifactDownloadPath && <a className="button secondary" href={bootstrap.artifactDownloadPath}><Download /> Download {bootstrap.firmwareFilename ?? 'firmware.bin'}</a>}
              <ol>
                <li>Connect the ESP32 by USB and replace &lt;PORT&gt; with its COM port.</li>
                <li>Run the exact command above without an erase option.</li>
                <li>Confirm the sensor reconnects and reports OTA protocol v2.</li>
              </ol>
            </div>
          </div>
        )}

        {deployment && <FirmwareDeploymentStatus deployment={deployment} />}
        {errors && <InlineNotice tone="danger">{errorMessage(errors)}</InlineNotice>}
      </div>
      <footer>
        <button className="button secondary" type="button" disabled={busy} onClick={onClose}>Close</button>
        {!release && !deployment && <button className="button primary" type="button" disabled={!file || upload.isPending} onClick={() => { upload.mutate() }}>{upload.isPending ? 'Server verification…' : 'Verify firmware'}</button>}
        {release && !deployment && !bootstrapRequired && <button className="button primary" type="button" disabled={!installReady || install.isPending} onClick={() => { install.mutate() }}>{install.isPending ? 'Scheduling…' : 'Install'}</button>}
        {deployment && firmwareCancellableStates.has(deployment.state) && <button className="button danger" type="button" disabled={busy} onClick={() => { cancel.mutate(deployment.id) }}>Cancel update</button>}
        {deployment && firmwareRetryableStates.has(deployment.state) && <button className="button primary" type="button" disabled={busy} onClick={() => { retry.mutate(deployment.id) }}>Retry</button>}
        {deployment && firmwareTerminalStates.has(deployment.state) && <button className="button secondary" type="button" disabled={busy} onClick={startAnother}>Start another update</button>}
      </footer>
    </section>
  )
}
