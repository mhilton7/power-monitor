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
    expect(await screen.findByText('Installed and verified')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Start another update' }))
    expect(screen.getByText('Choose firmware.bin')).toBeVisible()
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

    expect(await screen.findByText('Update failed · previous firmware restored')).toBeVisible()
    expect(screen.getByText('post_boot_validation_failed')).toBeVisible()
    expect(screen.getByText(/previous firmware 1.0.10 was restored/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeVisible()
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
    expect(await screen.findByText('Cancelled')).toBeVisible()
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
