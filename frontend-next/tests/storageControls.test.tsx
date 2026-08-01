import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { SensorStorageContent } from '../src/pages/settings/SettingsPage'
import type { SensorStoragePolicy, SensorStorageStatus } from '../src/types/models'

const policy: SensorStoragePolicy = {
  retentionMode: 'continuous_protected',
  retentionDays: 730,
  minimumLocalHistoryDays: 30,
  noticePercent: 20,
  warningPercent: 10,
  criticalPercent: 5,
  emergencyPercent: 2,
  emergencyReserveBytes: 536_870_912,
  cleanupTargetPercent: 10,
  cleanupTargetBytes: 1_073_741_824,
  eventRetentionDays: 730,
}

function status(overrides: Partial<SensorStorageStatus> = {}): SensorStorageStatus {
  return {
    schemaVersion: 'sensor-storage/1.0',
    deviceId: 'sensor-1',
    deviceName: 'Indoor AC',
    observedAt: '2026-07-31T19:00:00Z',
    available: true,
    healthy: true,
    status: 'healthy',
    pressureState: 'healthy',
    cardType: 'SDHC',
    capacityBytes: 32_000_000_000,
    usedBytes: 4_000_000_000,
    freeBytes: 28_000_000_000,
    freePercent: 87.5,
    storageFull: false,
    preparedForRemoval: false,
    newestStoredSequence: 900,
    serverAckSequence: 850,
    unsynchronizedCount: 50,
    eligibleReclaimableBytes: 128_000_000,
    blockedUnacknowledgedBytes: 64_000_000,
    eligibleSegmentCount: 4,
    protectedSegmentCount: 2,
    eventSegmentCount: 3,
    temporaryArtifactCount: 1,
    exportCount: 2,
    repairArtifactCount: 0,
    cleanupInProgress: false,
    cleanupRecoveryRequired: false,
    droppedIntervalCount: 0,
    desiredPolicy: policy,
    effectivePolicy: policy,
    desiredConfigVersion: 8,
    effectiveConfigVersion: 8,
    policyPending: false,
    ...overrides,
  }
}

function renderStorage(value: SensorStorageStatus, canManage: boolean) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={client}>
      <SensorStorageContent status={value} canManage={canManage} refresh={vi.fn().mockResolvedValue(undefined)} />
    </QueryClientProvider>,
  )
}

describe('sensor storage controls', () => {
  it('shows evidence but no mutation actions to a storage viewer', () => {
    renderStorage(status(), false)

    expect(screen.getByText('SDHC', { exact: false })).toBeInTheDocument()
    expect(screen.getByText(/850 \/ 900/)).toBeInTheDocument()
    expect(screen.getByText(/4 eligible/)).toBeInTheDocument()
    expect(screen.getByText('Event segments').parentElement).toHaveTextContent('3')
    expect(screen.getByLabelText('Notice percent')).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Apply storage policy' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Run safe cleanup' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Prepare for removal' })).not.toBeInTheDocument()
  })

  it('exposes policy and safe-card actions only to a storage manager', () => {
    renderStorage(status(), true)

    expect(screen.getByRole('button', { name: 'Apply storage policy' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Run safe cleanup' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Prepare for removal' })).toBeDisabled()
    expect(screen.getByText(/Power down before physically removing it/)).toBeInTheDocument()
  })

  it.each([
    'notice',
    'warning',
    'critical',
    'emergency',
    'full',
    'blocked',
    'read_only',
    'failed',
    'prepared',
  ])('renders the %s storage state without hiding evidence', (pressureState) => {
    const view = renderStorage(status({ healthy: false, pressureState, status: pressureState }), false)

    expect(view.container.querySelector('.storage-status-heading .pill')).toHaveTextContent(
      new RegExp(pressureState.replace('_', ' '), 'i'),
    )
    expect(screen.getByText('Unsynchronized readings')).toBeInTheDocument()
  })

  it('makes recovery blocks and durable gaps explicit', () => {
    renderStorage(status({
      healthy: false,
      pressureState: 'blocked',
      cleanupRecoveryRequired: true,
      cleanupInProgress: true,
      droppedIntervalCount: 3,
      firstDroppedIntervalAt: '2026-07-31T18:00:00Z',
    }), true)

    expect(screen.getByText(/Cleanup recovery is blocked/)).toBeInTheDocument()
    expect(screen.getByText(/cleanup is running/)).toBeInTheDocument()
    expect(screen.getByText(/explicit storage gap/)).toBeInTheDocument()
    expect(screen.getByText(/3 intervals/)).toBeInTheDocument()
  })
})
