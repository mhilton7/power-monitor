import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, RefreshCw, ShieldCheck } from 'lucide-react'
import { useMemo, useState } from 'react'
import { adaptBillingCycle, adaptUsageAuthority } from '../../api/adapters'
import { errorMessage, json, request } from '../../api/client'
import { Surface } from '../../components/data-display/Surface'
import { InlineNotice, LoadingState } from '../../components/feedback/States'
import type {
  BillingCycleSummary,
  ElectricService,
  SensorSummary,
  UsageAuthority,
} from '../../types/models'
import { statusLabel } from '../../utils/format'

type AuthorityMode =
  | 'manual_cycle_usage'
  | 'whole_account_meter'
  | 'partial_monitored_circuits'

function supportedMode(value?: string): AuthorityMode {
  if (
    value === 'whole_account_meter'
    || value === 'partial_monitored_circuits'
    || value === 'manual_cycle_usage'
  ) return value
  return 'manual_cycle_usage'
}

export function CostCalculationSetup({
  service,
  sensors,
  cycle,
  latestBillId,
  onRefresh,
}: {
  service: ElectricService
  sensors: SensorSummary[]
  cycle?: BillingCycleSummary
  latestBillId?: string
  onRefresh: () => Promise<void>
}) {
  const authority = useQuery({
    queryKey: ['usage-authority', service.id],
    queryFn: () => request(
      `/api/v1/admin/utility-accounts/${service.id}/usage-authority`,
      {},
      adaptUsageAuthority,
    ),
  })

  return (
    <Surface
      className="cost-calculation-setup"
      title="Tiered cost calculation"
      subtitle="Whole-account usage authority and chronological billing-cycle allocation"
    >
      {authority.isLoading ? <LoadingState label="Loading cost calculation setup…" /> : authority.error ? (
        <InlineNotice tone="danger">
          {errorMessage(authority.error)}
          <button type="button" className="button secondary compact" onClick={() => { void authority.refetch() }}>
            Try again
          </button>
        </InlineNotice>
      ) : (
        <AuthorityEditor
          key={`${authority.data?.revision ?? 0}-${latestBillId ?? 'no-bill'}`}
          service={service}
          sensors={sensors}
          cycle={cycle}
          latestBillId={latestBillId}
          authority={authority.data}
          onSaved={async () => {
            await authority.refetch()
            await onRefresh()
          }}
        />
      )}
    </Surface>
  )
}

function AuthorityEditor({
  service,
  sensors,
  cycle,
  latestBillId,
  authority,
  onSaved,
}: {
  service: ElectricService
  sensors: SensorSummary[]
  cycle?: BillingCycleSummary
  latestBillId?: string
  authority?: UsageAuthority
  onSaved: () => Promise<void>
}) {
  const client = useQueryClient()
  const assignedSensors = useMemo(
    () => sensors.filter((sensor) => sensor.utilityAccountId === service.id),
    [sensors, service.id],
  )
  const [mode, setMode] = useState<AuthorityMode>(supportedMode(authority?.authorityType))
  const [selectedDeviceIds, setSelectedDeviceIds] = useState<string[]>(
    authority?.deviceIds.length
      ? authority.deviceIds
      : assignedSensors.map((sensor) => sensor.id),
  )
  const [notice, setNotice] = useState('')

  const needsBillContext = mode === 'manual_cycle_usage'
    || mode === 'partial_monitored_circuits'
  const validSelection = mode === 'manual_cycle_usage'
    ? Boolean(latestBillId || authority?.sourceReference)
    : mode === 'whole_account_meter'
      ? selectedDeviceIds.length === 1
      : selectedDeviceIds.length > 0 && Boolean(latestBillId || authority?.sourceReference)

  const saveAndRecalculate = useMutation({
    mutationFn: async () => {
      const sourceReference = latestBillId
        ? `utility-bill:${latestBillId}`
        : authority?.sourceReference
      await request(
        `/api/v1/admin/utility-accounts/${service.id}/usage-authority`,
        json('PUT', {
          revision: authority?.configured ? authority.revision : null,
          authority_type: mode,
          aggregate_set_id: null,
          device_ids: mode === 'manual_cycle_usage' ? [] : selectedDeviceIds,
          source_reference: sourceReference,
          confidence: latestBillId ? 'utility_verified' : authority?.confidence ?? 'unverified',
          complete_account: mode !== 'partial_monitored_circuits',
        }),
      )
      return request(
        `/api/v1/admin/utility-accounts/${service.id}/billing-cycles/current/recalculate`,
        json('POST'),
        adaptBillingCycle,
      )
    },
    onSuccess: async (result) => {
      setNotice(
        result.available
          ? `Chronological allocation recalculated at version ${result.recalculationVersion}.`
          : result.warnings[0] ?? 'The authority was saved, but more billing context is required.',
      )
      await Promise.all([
        client.invalidateQueries({ queryKey: ['billing-cycle-summary', service.id] }),
        client.invalidateQueries({ queryKey: ['configuration-status', service.homeId] }),
        client.invalidateQueries({ queryKey: ['history'] }),
        client.invalidateQueries({ queryKey: ['home-summary', service.homeId] }),
      ])
      await onSaved()
    },
  })

  const recalculate = useMutation({
    mutationFn: () => request(
      `/api/v1/admin/utility-accounts/${service.id}/billing-cycles/current/recalculate`,
      json('POST'),
      adaptBillingCycle,
    ),
    onSuccess: async (result) => {
      setNotice(
        result.available
          ? `Chronological allocation recalculated at version ${result.recalculationVersion}.`
          : result.warnings[0] ?? 'More billing context is required.',
      )
      await Promise.all([
        client.invalidateQueries({ queryKey: ['billing-cycle-summary', service.id] }),
        client.invalidateQueries({ queryKey: ['configuration-status', service.homeId] }),
        client.invalidateQueries({ queryKey: ['history'] }),
        client.invalidateQueries({ queryKey: ['home-summary', service.homeId] }),
      ])
      await onSaved()
    },
  })

  const changing = saveAndRecalculate.isPending || recalculate.isPending
  const error = saveAndRecalculate.error ?? recalculate.error
  return (
    <div className="cost-setup-content">
      <div className="workflow-security">
        <ShieldCheck />
        <span>
          <strong>
            {authority?.configured
              ? `${statusLabel(authority.authorityType ?? 'manual_cycle_usage')} · ${statusLabel(authority.confidence)}`
              : 'Usage authority not configured'}
          </strong>
          <small>
            Finalized cycles are immutable. Only the current unfinalized cycle is recalculated.
          </small>
        </span>
      </div>
      <div className="form-grid">
        <label className="span-all">
          <span>How should whole-account tier progress be established?</span>
          <select
            value={mode}
            onChange={(event) => {
              const nextMode = event.target.value as AuthorityMode
              setMode(nextMode)
              if (nextMode === 'whole_account_meter' && selectedDeviceIds.length > 1) {
                setSelectedDeviceIds(selectedDeviceIds.slice(0, 1))
              }
            }}
          >
            <option value="manual_cycle_usage">Reviewed utility bill or manual account usage</option>
            <option value="whole_account_meter">One whole-home meter sensor</option>
            <option value="partial_monitored_circuits">Partial circuits with utility-bill context</option>
          </select>
        </label>
        {mode !== 'manual_cycle_usage' && (
          <fieldset className="choice-grid span-all">
            <legend>{mode === 'whole_account_meter' ? 'Whole-home sensor' : 'Monitored branch sensors'}</legend>
            {assignedSensors.length ? assignedSensors.map((sensor) => {
              const selected = selectedDeviceIds.includes(sensor.id)
              return (
                <label className="choice-card" key={sensor.id}>
                  <input
                    type={mode === 'whole_account_meter' ? 'radio' : 'checkbox'}
                    name="usage-authority-sensors"
                    checked={selected}
                    onChange={() => {
                      setSelectedDeviceIds(
                        mode === 'whole_account_meter'
                          ? [sensor.id]
                          : selected
                            ? selectedDeviceIds.filter((id) => id !== sensor.id)
                            : [...selectedDeviceIds, sensor.id],
                      )
                    }}
                  />
                  <span>
                    <strong>{sensor.name}</strong>
                    <small>{sensor.monitoredCircuit} · {statusLabel(sensor.measurementRole)}</small>
                  </span>
                </label>
              )
            }) : (
              <InlineNotice tone="warning">
                Assign a sensor to this electric service under Settings → Sensors first.
              </InlineNotice>
            )}
          </fieldset>
        )}
      </div>
      {needsBillContext && !latestBillId && !authority?.sourceReference && (
        <InlineNotice tone="warning">
          Upload and approve an electric bill before using this authority. A branch sensor
          cannot determine the household tier by itself.
        </InlineNotice>
      )}
      {cycle?.finalizedAt && (
        <InlineNotice tone="warning">
          The displayed cycle is finalized and cannot be changed. The next current cycle
          will remain eligible for recalculation.
        </InlineNotice>
      )}
      {cycle?.warnings.map((warning) => <InlineNotice key={warning}>{warning}</InlineNotice>)}
      {notice && <InlineNotice tone="success"><Check /> {notice}</InlineNotice>}
      {error && <InlineNotice tone="danger">{errorMessage(error)}</InlineNotice>}
      <div className="form-actions">
        <button
          type="button"
          className="button primary"
          disabled={!validSelection || changing || Boolean(cycle?.finalizedAt)}
          onClick={() => { saveAndRecalculate.mutate() }}
        >
          <RefreshCw /> {changing ? 'Calculating…' : 'Save and recalculate'}
        </button>
        {authority?.configured && (
          <button
            type="button"
            className="button secondary"
            disabled={changing || Boolean(cycle?.finalizedAt)}
            onClick={() => { recalculate.mutate() }}
          >
            <RefreshCw /> Recalculate current cycle
          </button>
        )}
      </div>
    </div>
  )
}
