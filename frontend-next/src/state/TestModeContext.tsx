import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, type ReactNode } from 'react'
import { adaptTestMode } from '../api/adapters'
import { json, request } from '../api/client'
import { hasPermission } from '../access/permissions'
import type { TestLoadProfile, TestModeState } from '../types/models'
import { useAuth } from './AuthContext'

interface TestModeConfiguration {
  sensorCount: number
  loadProfile: TestLoadProfile
  offlineSensorIndexes: number[]
  customLoadW?: number
  baseLoadW: number
  variationPercent: number
  sampleIntervalSeconds: number
  expiresInMinutes: number | null
  costPreviewEnabled: boolean
  paused: boolean
  siteId?: string
}

interface TestModeValue {
  state?: TestModeState
  loading: boolean
  changing: boolean
  error: unknown
  refresh: () => Promise<void>
  enable: (configuration: TestModeConfiguration) => Promise<TestModeState>
  update: (configuration: Partial<TestModeConfiguration>) => Promise<TestModeState>
  disable: () => Promise<TestModeState>
  reset: () => Promise<TestModeState>
}

const TestModeContext = createContext<TestModeValue | undefined>(undefined)

function operationKey(): string {
  return crypto.randomUUID()
}

export function TestModeProvider({ children }: { children: ReactNode }) {
  const { session } = useAuth()
  const client = useQueryClient()
  const owner = hasPermission(session, 'settings.manage')
  const query = useQuery({
    queryKey: ['sensor-test-mode'],
    queryFn: () => request('/api/v1/test-mode', {}, adaptTestMode),
    enabled: owner,
    retry: false,
    refetchInterval: (result) => result.state.data?.enabled ? 5_000 : 30_000,
  })
  const change = useMutation({
    mutationFn: async ({
      action,
      configuration,
    }: {
      action: 'enable' | 'update' | 'disable' | 'reset'
      configuration?: Partial<TestModeConfiguration>
    }) => {
      const payload = configuration ? {
        sensor_count: configuration.sensorCount,
        load_profile: configuration.loadProfile,
        offline_sensor_indexes: configuration.offlineSensorIndexes,
        custom_load_w: configuration.customLoadW,
        base_load_w: configuration.baseLoadW,
        variation_percent: configuration.variationPercent,
        sample_interval_seconds: configuration.sampleIntervalSeconds,
        expires_in_minutes: configuration.expiresInMinutes,
        cost_preview_enabled: configuration.costPreviewEnabled,
        paused: configuration.paused,
        site_id: configuration.siteId,
        idempotency_key: operationKey(),
      } : { idempotency_key: operationKey() }
      const path = action === 'enable'
        ? '/api/v1/test-mode/enable'
        : action === 'update'
          ? '/api/v1/test-mode'
          : `/api/v1/test-mode/${action}`
      const method = action === 'update' ? 'PUT' : 'POST'
      return request(path, json(method, payload), adaptTestMode)
    },
    onSuccess: (state) => {
      client.setQueryData(['sensor-test-mode'], state)
      void client.invalidateQueries({ queryKey: ['sensor-test-mode-sensors'] })
      void client.invalidateQueries({ queryKey: ['sensor-test-mode-history'] })
      void client.invalidateQueries({ queryKey: ['home-summary'] })
      void client.invalidateQueries({ queryKey: ['history'] })
      void client.invalidateQueries({ queryKey: ['sensors'] })
      void client.invalidateQueries({ queryKey: ['billing-cycle-summary'] })
    },
  })
  return (
    <TestModeContext.Provider value={{
      state: query.data,
      loading: query.isLoading,
      changing: change.isPending,
      error: query.error ?? change.error,
      refresh: async () => { await query.refetch() },
      enable: (configuration) => change.mutateAsync({ action: 'enable', configuration }),
      update: (configuration) => change.mutateAsync({ action: 'update', configuration }),
      disable: () => change.mutateAsync({ action: 'disable' }),
      reset: () => change.mutateAsync({ action: 'reset' }),
    }}>
      {children}
    </TestModeContext.Provider>
  )
}

export function useTestMode(): TestModeValue {
  const value = useContext(TestModeContext)
  if (!value) throw new Error('TestModeProvider is missing')
  return value
}
