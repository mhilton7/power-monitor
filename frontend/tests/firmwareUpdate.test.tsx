import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { FirmwareUpdateDialog } from '../src/features/firmware/FirmwareUpdateDialog'
import type { SensorSummary } from '../src/types/models'

const jsonHeaders = { 'Content-Type': 'application/json' }

function sensor(overrides: Partial<SensorSummary> = {}): SensorSummary {
  return {
    id: 'sensor-outdoor',
    name: 'Outdoor-AC',
    homeId: 'home-1',
    state: 'live',
    deviceStatus: 'online_synchronized',
    online: true,
    measurementFreshness: 'live',
    heartbeatFreshness: 'online',
    offlineAfterSeconds: 45,
    invalidMetrics: [],
    firmware: '1.0.10',
    monitoredCircuit: 'Outdoor condenser',
    includedInDefault: true,
    backlog: 0,
    ctRatingAmps: '100',
    measurementRole: 'branch',
    firmwareOta: {
      state: 'ready',
      supported: true,
      protocolVersion: 2,
      authenticationMode: 'existing_device_hmac',
      rollbackSupported: true,
      partitionSizeBytes: 6_291_456,
    },
    ...overrides,
  }
}

function release() {
  return {
    id: 'release-11', version: '1.0.11', project_name: 'power-monitor-sensor',
    hardware_target: 'esp32-s3', protocol_min: 'pm-protocol/1.0.0',
    protocol_max: 'pm-protocol/1.0.0', size_bytes: 1_650_000,
    sha256: 'b'.repeat(64), build_hash: 'build-11', trust_mode: 'existing_device_hmac',
    verification_status: 'verified', active: true,
  }
}

function readiness(state: 'ready' | 'bootstrap_required') {
  const ready = state === 'ready'
  return {
    device_id: 'sensor-outdoor',
    current_firmware_version: '1.0.10',
    firmware_ota: {
      state,
      supported: ready,
      protocol_version: ready ? 2 : null,
      authentication_mode: ready ? 'existing_device_hmac' : null,
      rollback_supported: ready,
      partition_size_bytes: ready ? 6_291_456 : null,
    },
    release_id: 'release-11',
    compatibility: { ready, reasons: ready ? [] : ['bootstrap_required'] },
    bootstrap: {
      required: !ready,
      firmware_filename: 'power-monitor-sensor-1.0.11.bin',
      sha256: 'b'.repeat(64),
      expected_version: '1.0.11',
      expected_build_hash: 'build-11',
      artifact_download_path: '/api/v1/firmware-releases/release-11/artifact',
      usb_command: 'python -m esptool --chip esp32s3 --port <PORT> --baud 460800 write_flash 0x20000 power-monitor-sensor-1.0.11.bin',
      preserves: ['NVS', 'Wi-Fi', 'enrollment', 'CA', 'microSD', 'sequence'],
    },
  }
}

function deployment(state = 'scheduled') {
  return {
    id: 'deployment-1', firmware_release_id: 'release-11', device_id: 'sensor-outdoor',
    state, revision: 2, attempt: 1, progress: state === 'completed' ? 100 : 20,
    bytes_received: state === 'completed' ? 1_650_000 : 330_000, target_version: '1.0.11',
  }
}

function view(value: SensorSummary) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><FirmwareUpdateDialog sensor={value} onClose={vi.fn()} /></QueryClientProvider>)
}

function chooseFirmware(name = 'firmware.bin') {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]')
  expect(input).not.toBeNull()
  fireEvent.change(input as HTMLInputElement, { target: { files: [new File(['firmware'], name, { type: 'application/octet-stream' })] } })
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.toString()
  return input.url
}

afterEach(() => { vi.unstubAllGlobals() })

describe('existing-trust firmware workflow', () => {
  it('derives an honest one-time non-erasing bootstrap only after server verification', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = requestUrl(input)
      if (url.startsWith('/api/v1/firmware-deployments')) return Promise.resolve(new Response('[]', { status: 200, headers: jsonHeaders }))
      if (url === '/api/v1/firmware-releases') return Promise.resolve(new Response(JSON.stringify(release()), { status: 201, headers: jsonHeaders }))
      if (url.includes('/firmware-readiness?release_id=release-11')) return Promise.resolve(new Response(JSON.stringify(readiness('bootstrap_required')), { status: 200, headers: jsonHeaders }))
      throw new Error(`Unexpected request ${url}`)
    }))
    const user = userEvent.setup()
    view(sensor({ firmwareOta: { state: 'bootstrap_required', supported: false } }))

    expect(await screen.findByText(/needs one non-erasing USB bootstrap/)).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'One-time OTA bootstrap required' })).not.toBeInTheDocument()
    chooseFirmware()
    await user.click(screen.getByRole('button', { name: 'Verify firmware' }))

    expect(await screen.findByRole('heading', { name: 'One-time OTA bootstrap required' })).toBeVisible()
    expect(screen.getByText(/Wi-Fi, NVS settings, the trusted CA, microSD history, and sequence state remain intact/)).toBeVisible()
    expect(screen.getByText(/write_flash 0x20000 power-monitor-sensor-1.0.11.bin/)).toBeVisible()
    expect(screen.getByRole('link', { name: /Download power-monitor-sensor-1.0.11.bin/ })).toHaveAttribute('href', '/api/v1/firmware-releases/release-11/artifact')
    expect(screen.queryByRole('button', { name: 'Install' })).not.toBeInTheDocument()
  })

  it('accepts one firmware.bin, reviews server-derived metadata, then creates one-device deployment', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      calls.push({ url, init })
      if (url.startsWith('/api/v1/firmware-deployments') && (!init?.method || init.method === 'GET')) return Promise.resolve(new Response('[]', { status: 200, headers: jsonHeaders }))
      if (url === '/api/v1/firmware-releases') {
        expect(init?.body).toBeInstanceOf(FormData)
        expect((init?.body as FormData).get('binary')).toBeInstanceOf(File)
        return Promise.resolve(new Response(JSON.stringify(release()), { status: 201, headers: jsonHeaders }))
      }
      if (url.includes('/firmware-readiness?release_id=release-11')) return Promise.resolve(new Response(JSON.stringify(readiness('ready')), { status: 200, headers: jsonHeaders }))
      if (url === '/api/v1/firmware-deployments' && init?.method === 'POST') return Promise.resolve(new Response(JSON.stringify({ deployments: [deployment()] }), { status: 201, headers: jsonHeaders }))
      throw new Error(`Unexpected request ${url}`)
    }))
    const user = userEvent.setup()
    view(sensor())
    expect(screen.getByRole('dialog', { name: 'Update Outdoor-AC' })).toBeVisible()
    expect(screen.getByLabelText(/Choose firmware.bin/)).toHaveAttribute('type', 'file')
    await screen.findByText('Ready for server OTA')
    chooseFirmware()
    await user.click(screen.getByRole('button', { name: 'Verify firmware' }))

    expect(await screen.findByText('Firmware verified')).toBeVisible()
    expect(screen.getByText('1.0.11')).toBeVisible()
    expect(screen.getByText('Existing device HMAC')).toBeVisible()
    expect(screen.getByText('Existing trusted HTTPS')).toBeVisible()
    await waitFor(() => { expect(screen.getByRole('button', { name: 'Install' })).toBeEnabled() })
    await user.click(screen.getByRole('button', { name: 'Install' }))

    await waitFor(() => {
      const requestCall = calls.find((call) => call.url === '/api/v1/firmware-deployments' && call.init?.method === 'POST')
      expect(typeof requestCall?.init?.body).toBe('string')
      expect(JSON.parse(requestCall?.init?.body as string)).toMatchObject({
        device_ids: ['sensor-outdoor'], firmware_release_id: 'release-11',
        allow_downgrade: false, canary_first: true, maximum_concurrency: 1,
      })
    })
  })

  it('rejects a renamed application image before uploading it', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('[]', { status: 200, headers: jsonHeaders })))
    const user = userEvent.setup()
    view(sensor())
    await screen.findByText('Ready for server OTA')
    chooseFirmware('sensor.bin')
    await user.click(screen.getByRole('button', { name: 'Verify firmware' }))
    expect(await screen.findByText('Choose the application file named firmware.bin.')).toBeVisible()
  })

  it('restores the latest terminal deployment after a refresh and allows another update', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify([deployment('completed')]), { status: 200, headers: jsonHeaders })))
    const user = userEvent.setup()
    view(sensor({ firmware: '1.0.11' }))
    expect((await screen.findAllByText('Completed'))[0]).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Start another update' }))
    expect(screen.getByText('Choose firmware.bin')).toBeVisible()
  })

  it('renders zero-byte download startup as indeterminate instead of stale zero percent', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify([{
      ...deployment('download_started'),
      progress: 0,
      bytes_received: 0,
      progress_mode: 'indeterminate',
      display_state: 'starting_download',
      last_report_at: '2026-08-03T20:00:00Z',
    }]), { status: 200, headers: jsonHeaders })))
    view(sensor())

    expect(await screen.findByText('Downloading')).toBeVisible()
    expect(screen.getByText('Waiting for the next authenticated sensor progress report.')).toBeVisible()
    expect(screen.queryByText('0%')).not.toBeInTheDocument()
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('value')
  })

  it('offers a safe retry for failed deployments', async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      if (url.includes('/retry') && init?.method === 'POST') return Promise.resolve(new Response(JSON.stringify(deployment('scheduled')), { status: 200, headers: jsonHeaders }))
      return Promise.resolve(new Response(JSON.stringify([{ ...deployment('failed'), failure_code: 'download_timeout', failure_summary: 'Download timed out safely.' }]), { status: 200, headers: jsonHeaders }))
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    view(sensor())
    expect(await screen.findByText('download_timeout')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => { expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/retry'), expect.objectContaining({ method: 'POST' })) })
  })

  it('shows the restored firmware and a specific recovery action after rollback', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify([{
      ...deployment('rolled_back'),
      failure_code: 'post_boot_validation_failed',
      failure_summary: 'The new application did not complete post-boot validation.',
      rollback_version: '1.0.10',
    }]), { status: 200, headers: jsonHeaders })))
    view(sensor())

    expect((await screen.findAllByText('Rolled back'))[0]).toBeVisible()
    expect(screen.getByText('post_boot_validation_failed')).toBeVisible()
    expect(screen.getByText(/previous firmware 1.0.10 was restored/i)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeVisible()
  })

  it('renders the authoritative post-update checklist and exact current blocker', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify([{
      ...deployment('awaiting_heartbeat'),
      progress: 100,
      progress_mode: 'determinate',
      validated_version: '1.0.11',
      validated_build_hash: 'build-11',
      target_build_hash: 'build-11',
      verification_heartbeats: 4,
      last_report_at: '2026-08-03T20:05:00Z',
      verification: {
        checks: [
          { key: 'target_identity', label: 'Target firmware identity', status: 'passed', detail: 'Verified 1.0.11.', observed_at: '2026-08-03T20:04:00Z' },
          { key: 'pzem', label: 'PZEM measurement hardware', status: 'passed', detail: 'PZEM health passed.' },
          { key: 'storage', label: 'microSD storage', status: 'passed', detail: 'Storage health passed.' },
          { key: 'trusted_time', label: 'Trusted time', status: 'passed', detail: 'Time trust passed.' },
          { key: 'verification_heartbeats', label: 'Healthy verification heartbeats', status: 'pending', detail: '4 of 10 received.' },
          { key: 'post_update_reading', label: 'Post-update reading', status: 'pending', detail: 'Waiting for the first durable reading.' },
        ],
        blocker: {
          code: 'ota_waiting_post_update_reading', state: 'awaiting_heartbeat',
          title: 'Post-update reading', detail: 'Waiting for the first durable reading.', action: 'wait',
        },
        target_version_expected: '1.0.11', target_version_observed: '1.0.11',
        target_build_hash_expected: 'build-11', target_build_hash_observed: 'build-11',
        target_boot_id_observed: 'boot-target', previous_boot_stage: 'reboot_scheduled',
        previous_reset_reason: 'software_reset', rollback_state: 'not_detected',
        exact_failure_code: null, blocking_critical_alert_count: 0,
        verification_heartbeat_count: 4, verification_heartbeat_required: 10,
        last_sensor_activity_at: '2026-08-03T20:06:00Z', last_report_at: '2026-08-03T20:05:00Z',
        stabilization_elapsed_seconds: 45, stabilization_required_seconds: 90,
      },
    }]), { status: 200, headers: jsonHeaders })))
    view(sensor())

    expect(await screen.findByText('Waiting for first reading')).toBeVisible()
    expect(screen.getByText('Post-update verification')).toBeVisible()
    expect(screen.getByText('ota_waiting_post_update_reading')).toBeVisible()
    expect(screen.getByText('PZEM measurement hardware')).toBeVisible()
    expect(screen.getByText('boot-target')).toBeVisible()
    expect(screen.getByText('45 of 90 seconds')).toBeVisible()
    expect(screen.getByText('Software Reset')).toBeVisible()
  })

  it('terminalizes a timed-out update without an infinite spinner and keeps Retry actionable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify([{
      ...deployment('failed'),
      progress: 0,
      bytes_received: 0,
      progress_mode: 'indeterminate',
      terminal_at: '2026-08-03T21:00:00Z',
      failure_code: 'ota_sensor_did_not_return',
      failure_summary: 'The sensor did not return within the recovery window.',
      verification: {
        checks: [],
        blocker: {
          code: 'ota_sensor_did_not_return', state: 'failed', title: 'Firmware update did not complete',
          detail: 'The sensor did not return within the recovery window.', action: 'retry',
        },
        blocking_critical_alert_count: 0,
        verification_heartbeat_count: 0,
        verification_heartbeat_required: 10,
        stabilization_elapsed_seconds: 0, stabilization_required_seconds: 90,
      },
    }]), { status: 200, headers: jsonHeaders })))
    const { container } = view(sensor())

    expect((await screen.findAllByText('Failed'))[0]).toBeVisible()
    expect(screen.getAllByText('ota_sensor_did_not_return')[0]).toBeVisible()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeVisible()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(container.querySelector('.spin')).not.toBeInTheDocument()
  })

  it('cancels only while the current stage is safe and refreshes the persisted deployment', async () => {
    let current = deployment('scheduled')
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      if (url.includes('/cancel') && init?.method === 'POST') {
        current = { ...current, state: 'cancelled' }
        return Promise.resolve(new Response(JSON.stringify(current), { status: 200, headers: jsonHeaders }))
      }
      return Promise.resolve(new Response(JSON.stringify([current]), { status: 200, headers: jsonHeaders }))
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    view(sensor())

    await user.click(await screen.findByRole('button', { name: 'Cancel update' }))
    await waitFor(() => { expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/cancel'), expect.objectContaining({ method: 'POST' })) })
    expect((await screen.findAllByText('Cancelled'))[0]).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Cancel update' })).not.toBeInTheDocument()
  })

  it('requires explicit confirmation and sends allow_downgrade for an older verified release', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      calls.push({ url, init })
      if (url.startsWith('/api/v1/firmware-deployments') && (!init?.method || init.method === 'GET')) return Promise.resolve(new Response('[]', { status: 200, headers: jsonHeaders }))
      if (url === '/api/v1/firmware-releases') return Promise.resolve(new Response(JSON.stringify({ ...release(), version: '1.0.9' }), { status: 201, headers: jsonHeaders }))
      if (url.includes('/firmware-readiness?release_id=release-11')) return Promise.resolve(new Response(JSON.stringify({
        ...readiness('ready'),
        compatibility: { ready: false, reasons: ['downgrade_requires_confirmation'] },
      }), { status: 200, headers: jsonHeaders }))
      if (url === '/api/v1/firmware-deployments' && init?.method === 'POST') return Promise.resolve(new Response(JSON.stringify({ deployments: [deployment()] }), { status: 201, headers: jsonHeaders }))
      throw new Error(`Unexpected request ${url}`)
    }))
    const user = userEvent.setup()
    view(sensor())
    chooseFirmware()
    await user.click(screen.getByRole('button', { name: 'Verify firmware' }))

    const confirmation = await screen.findByRole('checkbox', { name: /Confirm intentional downgrade/ })
    expect(screen.getByRole('button', { name: 'Install' })).toBeDisabled()
    await user.click(confirmation)
    await user.click(screen.getByRole('button', { name: 'Install' }))
    await waitFor(() => {
      const requestCall = calls.find((call) => call.url === '/api/v1/firmware-deployments' && call.init?.method === 'POST')
      expect(JSON.parse(requestCall?.init?.body as string)).toMatchObject({ allow_downgrade: true })
    })
  })

  it('does not mislabel missing OTA capability as ready', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('[]', { status: 200, headers: jsonHeaders })))
    view(sensor({ firmwareOta: undefined }))
    expect(await screen.findByText('OTA unsupported')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Install' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Verify firmware' })).toBeDisabled()
  })
})
