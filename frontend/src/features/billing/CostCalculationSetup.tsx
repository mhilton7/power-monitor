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
  | 'whole_account_meter'
  | 'service_leg_pair'

function supportedMode(value?: string): AuthorityMode {
  if (
    value === 'whole_account_meter'
    || value === 'service_leg_pair'
  ) return value
  return 'whole_account_meter'
}

export function CostCalculationSetup({
  service,
  sensors,
  cycle,
  onRefresh,
}: {
  service: ElectricService
  sensors: SensorSummary[]
  cycle?: BillingCycleSummary
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
      title="How usage is measured"
      subtitle="Choose the Power Monitor sensors that represent the complete electric service"
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
          key={`${authority.data?.revision ?? 0}`}
          service={service}
          sensors={sensors}
          cycle={cycle}
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
  authority,
  onSaved,
}: {
  service: ElectricService
  sensors: SensorSummary[]
  cycle?: BillingCycleSummary
  authority?: UsageAuthority
  onSaved: () => Promise<void>
}) {
  const client = useQueryClient()
  const assignedSensors = useMemo(
    () => {
      const assignedActiveIds = new Set(
        authority?.accountAssignedSensors
          .filter((sensor) => sensor.lifecycle === 'active')
          .map((sensor) => sensor.id) ?? [],
      )
      return sensors.filter((sensor) => (
        assignedActiveIds.size > 0
          ? assignedActiveIds.has(sensor.id)
          : sensor.utilityAccountId === service.id
      ))
    },
    [authority?.accountAssignedSensors, sensors, service.id],
  )
  const eligibleWholeAccountIds = useMemo(
    () => new Set(authority?.eligibleWholeAccountSensors.map((sensor) => sensor.id) ?? []),
    [authority?.eligibleWholeAccountSensors],
  )
  const eligibleServiceLegIds = useMemo(
    () => new Set(authority?.eligibleServiceLegSensors.map((sensor) => sensor.id) ?? []),
    [authority?.eligibleServiceLegSensors],
  )
  const serviceLegSensors = assignedSensors.filter((sensor) => eligibleServiceLegIds.has(sensor.id))
  const [mode, setMode] = useState<AuthorityMode>(
    authority?.authorityType
      ? supportedMode(authority.authorityType)
      : eligibleServiceLegIds.size === 2
        ? 'service_leg_pair'
        : 'whole_account_meter',
  )
  const initialEligibleIds = mode === 'whole_account_meter'
    ? eligibleWholeAccountIds
    : eligibleServiceLegIds
  const [selectedDeviceIds, setSelectedDeviceIds] = useState<string[]>(
    authority?.deviceIds.length
      ? authority.deviceIds.filter((id) => initialEligibleIds.has(id))
      : serviceLegSensors.length === 2
        ? serviceLegSensors.map((sensor) => sensor.id)
        : assignedSensors
            .filter((sensor) => eligibleWholeAccountIds.has(sensor.id))
            .slice(0, 1)
            .map((sensor) => sensor.id),
  )
  const [notice, setNotice] = useState('')

  const eligibleIds = mode === 'whole_account_meter'
    ? eligibleWholeAccountIds
    : eligibleServiceLegIds
  const sanitizedSelectedDeviceIds = selectedDeviceIds.filter((id) => eligibleIds.has(id))
  const validSelection = mode === 'whole_account_meter'
    ? sanitizedSelectedDeviceIds.length === 1
    : sanitizedSelectedDeviceIds.length === 2

  const saveAndRecalculate = useMutation({
    mutationFn: async () => {
      await request(
        `/api/v1/admin/utility-accounts/${service.id}/usage-authority`,
        json('PUT', {
          revision: authority?.configured ? authority.revision : null,
          authority_type: mode,
          aggregate_set_id: null,
          device_ids: sanitizedSelectedDeviceIds,
          source_reference: null,
          confidence: 'high',
          complete_account: true,
          calculation_role: 'sensor_measurements',
          reason: 'Reviewed current complete-service sensor topology',
          idempotency_key: crypto.randomUUID(),
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
          ? `Sensor usage and chronological tier allocation recalculated at version ${result.recalculationVersion}.`
          : result.warnings[0] ?? 'The sensor selection was saved, but more readings are required.',
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
          ? `Sensor usage and chronological tier allocation recalculated at version ${result.recalculationVersion}.`
          : result.warnings[0] ?? 'More sensor readings are required.',
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
              ? authority.calculationRole === 'sensor_measurements'
                ? `Power Monitor sensors · ${statusLabel(authority.authorityType ?? 'whole_account_meter')}`
                : 'Previous external usage source requires review'
              : 'Sensor usage source not configured'}
          </strong>
          <small>
            Rates come from the selected rate plan. Usage, tier progress, and projections
            come from accepted sensor readings.
          </small>
        </span>
      </div>
      <div className="form-grid">
        <label className="span-all">
          <span>Complete-service measurement</span>
          <select
            value={mode}
            onChange={(event) => {
              const nextMode = event.target.value as AuthorityMode
              setMode(nextMode)
              const nextEligibleIds = nextMode === 'whole_account_meter'
                ? eligibleWholeAccountIds
                : eligibleServiceLegIds
              const retained = selectedDeviceIds.filter((id) => nextEligibleIds.has(id))
              if (nextMode === 'whole_account_meter') {
                setSelectedDeviceIds(retained.slice(0, 1))
              } else {
                setSelectedDeviceIds(
                  retained.length > 0
                    ? retained.slice(0, 2)
                    : serviceLegSensors.slice(0, 2).map((sensor) => sensor.id),
                )
              }
            }}
          >
            <option value="whole_account_meter">One whole-home meter sensor</option>
            <option value="service_leg_pair">Two non-overlapping service-leg sensors</option>
          </select>
        </label>
        <fieldset className="choice-grid span-all">
            <legend>{mode === 'whole_account_meter' ? 'Whole-home sensor' : 'Service-leg sensors'}</legend>
            {assignedSensors.length ? assignedSensors.map((sensor) => {
              const selected = selectedDeviceIds.includes(sensor.id)
              const eligible = eligibleIds.has(sensor.id)
              const eligibility = authority?.accountAssignedSensors.find((item) => item.id === sensor.id)
              const eligibilityReason = mode === 'whole_account_meter'
                ? eligibility?.wholeAccountReason
                : eligibility?.serviceLegReason
              return (
                <label className="choice-card" key={sensor.id}>
                  <input
                    type={mode === 'whole_account_meter' ? 'radio' : 'checkbox'}
                    name="usage-authority-sensors"
                    checked={selected}
                    disabled={
                      !eligible
                      || (mode === 'service_leg_pair'
                        && !selected
                        && sanitizedSelectedDeviceIds.length >= 2)
                    }
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
                    <small>
                      {sensor.monitoredCircuit} · {statusLabel(sensor.measurementRole)}
                      {!eligible && eligibilityReason ? ` · ${statusLabel(eligibilityReason)}` : ''}
                    </small>
                  </span>
                </label>
              )
            }) : (
              <InlineNotice tone="warning">
                Assign a sensor to this electric service under Settings → Sensors first.
              </InlineNotice>
            )}
          </fieldset>
      </div>
      {!validSelection && (
        <InlineNotice tone="warning">
          Usage source needs attention. Select {mode === 'whole_account_meter'
            ? 'one sensor that measures the complete service'
            : 'exactly two non-overlapping service-leg sensors'} to calculate tiered costs.
        </InlineNotice>
      )}
      {Boolean(authority?.invalidDevices.length) && (
        <InlineNotice tone="warning">
          The saved usage source references a sensor that is no longer active, assigned,
          or eligible. Choose the current sensors and save to repair the configuration.
          {' '}{authority?.invalidDevices.map((item) => item.name).filter(Boolean).join(', ')}
        </InlineNotice>
      )}
      {authority?.configured && authority.calculationRole !== 'sensor_measurements' && (
        <InlineNotice tone="warning">
          The previous bill or external usage source is excluded from normal calculations.
          Save a reviewed sensor selection to recalculate the unfinalized cycle.
        </InlineNotice>
      )}
      {cycle?.finalizedAt && (
        <InlineNotice tone="warning">
          The displayed cycle is finalized and cannot be changed. The next current cycle
          will remain eligible for recalculation.
        </InlineNotice>
      )}
      {cycle?.warnings.map((warning) => <InlineNotice key={warning}>{warning}</InlineNotice>)}
      <details className="advanced-usage-corrections">
        <summary>Advanced usage corrections and external meter data</summary>
        <InlineNotice tone="warning">
          Advanced corrections change tier progression. They are not required for normal
          sensor-based monitoring and cannot be created by uploading a bill.
        </InlineNotice>
        <p>
          Administrators can manage explicitly confirmed external interval data,
          cumulative corrections, reversals, and provenance in Detailed Rates.
        </p>
      </details>
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
