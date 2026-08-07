import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Radio, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { adaptCircuits } from '../../api/adapters'
import { errorMessage, json, request } from '../../api/client'
import { InlineNotice, LoadingState } from '../../components/feedback/States'
import type {
  CircuitSummary,
  ElectricService,
  Home,
  SensorSummary,
} from '../../types/models'

const roles: Array<[CircuitSummary['measurementRole'], string]> = [
  ['main', 'Whole-home main'],
  ['service-leg', 'Service leg'],
  ['branch', 'Circuit or appliance'],
  ['submeter', 'Submeter'],
  ['informational', 'Informational only'],
]

function supportedRole(value: string): CircuitSummary['measurementRole'] {
  return roles.some(([role]) => role === value)
    ? value as CircuitSummary['measurementRole']
    : 'branch'
}

export function MeasurementAssignmentDialog({
  home,
  sensor,
  services,
  onClose,
  onDone,
}: {
  home: Home
  sensor: SensorSummary
  services: ElectricService[]
  onClose: () => void
  onDone: () => void
}) {
  const client = useQueryClient()
  const circuits = useQuery({
    queryKey: ['circuits', home.id],
    queryFn: () => request(
      `/api/v1/circuits?site_id=${encodeURIComponent(home.id)}`,
      {},
      adaptCircuits,
    ),
  })
  const [circuitChoice, setCircuitChoice] = useState(sensor.circuitId ?? '__new__')
  const [newCircuitName, setNewCircuitName] = useState(
    sensor.circuitId ? '' : sensor.name,
  )
  const [newCircuitRole, setNewCircuitRole] = useState<CircuitSummary['measurementRole']>(
    supportedRole(sensor.measurementRole),
  )
  const [serviceId, setServiceId] = useState(
    sensor.utilityAccountId ?? services[0]?.id ?? '',
  )
  const [includeInHome, setIncludeInHome] = useState(sensor.includedInDefault)
  const [reason, setReason] = useState('Owner reviewed the sensor measurement boundary')
  const hasAssignment = Boolean(
    sensor.circuitId || sensor.utilityAccountId || sensor.includedInDefault,
  )

  const selectedCircuit = useMemo(
    () => circuits.data?.find((item) => item.id === circuitChoice),
    [circuitChoice, circuits.data],
  )
  const effectiveRole = circuitChoice === '__new__'
    ? newCircuitRole
    : selectedCircuit?.measurementRole
  const canSubmit = Boolean(
    serviceId
    && reason.trim().length >= 3
    && (
      (circuitChoice === '__new__' && newCircuitName.trim())
      || selectedCircuit
    ),
  )

  const save = useMutation({
    mutationFn: async () => {
      let circuitId = circuitChoice
      if (circuitChoice === '__new__') {
        const created = await request(
          '/api/v1/circuits',
          json('POST', {
            site_id: home.id,
            parent_id: null,
            name: newCircuitName.trim(),
            measurement_role: newCircuitRole,
            split_phase_group: null,
          }),
          (value) => adaptCircuits([value])[0],
        )
        if (!created) throw new Error('The server did not return the new circuit.')
        circuitId = created.id
      }
      return request(
        `/api/v1/admin/devices/${sensor.id}/measurement-assignment`,
        json('PUT', {
          circuit_id: circuitId,
          utility_account_id: serviceId,
          include_in_default_site_total: includeInHome,
          reason,
        }),
      )
    },
    onSuccess: async () => {
      await invalidateAssignmentQueries()
      onDone()
    },
  })
  const unassign = useMutation({
    mutationFn: () => request(
      `/api/v1/admin/devices/${sensor.id}/measurement-assignment`,
      json('PUT', {
        circuit_id: null,
        utility_account_id: null,
        include_in_default_site_total: false,
        reason,
      }),
    ),
    onSuccess: async () => {
      await invalidateAssignmentQueries()
      onDone()
    },
  })

  async function invalidateAssignmentQueries() {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['circuits', home.id] }),
      client.invalidateQueries({ queryKey: ['sensors', home.id] }),
      client.invalidateQueries({ queryKey: ['electric-services', home.id] }),
      client.invalidateQueries({ queryKey: ['configuration-status', home.id] }),
      client.invalidateQueries({ queryKey: ['history'] }),
      client.invalidateQueries({ queryKey: ['home-summary', home.id] }),
    ])
  }

  return (
    <section
      className="workflow measurement-assignment-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="measurement-assignment-title"
    >
      <header className="workflow-header">
        <div>
          <p>Topology and cost relationship</p>
          <h2 id="measurement-assignment-title">Manage {sensor.name} assignment</h2>
        </div>
        <button type="button" className="icon-button" onClick={onClose} aria-label="Close assignment">
          <X />
        </button>
      </header>
      <div className="workflow-body">
        <div className="workflow-security">
          <Radio />
          <span>
            <strong>Historical readings remain unchanged</strong>
            <small>
              This records which physical circuit and electric service the sensor measures.
            </small>
          </span>
        </div>
        {circuits.isLoading ? <LoadingState label="Loading monitored circuits…" /> : circuits.error ? (
          <InlineNotice tone="danger">
            {errorMessage(circuits.error)}
            <button type="button" className="button secondary compact" onClick={() => { void circuits.refetch() }}>
              Try again
            </button>
          </InlineNotice>
        ) : (
          <div className="form-grid">
            <label className="span-all">
              <span>Monitored circuit</span>
              <select
                value={circuitChoice}
                onChange={(event) => { setCircuitChoice(event.target.value) }}
              >
                {circuits.data?.map((circuit) => (
                  <option key={circuit.id} value={circuit.id}>
                    {circuit.name} · {roles.find(([role]) => role === circuit.measurementRole)?.[1]}
                  </option>
                ))}
                <option value="__new__">Create a new circuit…</option>
              </select>
            </label>
            {circuitChoice === '__new__' && (
              <>
                <label>
                  <span>New circuit name</span>
                  <input
                    value={newCircuitName}
                    onChange={(event) => { setNewCircuitName(event.target.value) }}
                    placeholder="Indoor AC"
                  />
                </label>
                <label>
                  <span>Measurement boundary</span>
                  <select
                    value={newCircuitRole}
                    onChange={(event) => {
                      setNewCircuitRole(event.target.value as CircuitSummary['measurementRole'])
                    }}
                  >
                    {roles.map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                </label>
              </>
            )}
            <label className="span-all">
              <span>Electric service</span>
              <select value={serviceId} onChange={(event) => { setServiceId(event.target.value) }}>
                <option value="">Choose an electric service</option>
                {services.map((service) => (
                  <option key={service.id} value={service.id}>{service.name}</option>
                ))}
              </select>
            </label>
            <label className="toggle-row span-all">
              <span>
                <strong>Use for Home if topology is incomplete</strong>
                <small>
                  Complete non-overlapping circuits on the same electric service combine
                  automatically. Enable this explicit fallback only when it does not overlap a
                  parent, child, whole-home, or service-leg sensor.
                </small>
              </span>
              <input
                type="checkbox"
                checked={includeInHome}
                onChange={(event) => { setIncludeInHome(event.target.checked) }}
              />
            </label>
            <label className="span-all">
              <span>Change reason</span>
              <input value={reason} onChange={(event) => { setReason(event.target.value) }} />
            </label>
          </div>
        )}
        {effectiveRole === 'branch' && (
          <InlineNotice>
            A branch sensor remains energy-only. Tier progression must come from a reviewed
            whole-account bill, import, or meter.
          </InlineNotice>
        )}
        {save.error && <InlineNotice tone="danger">{errorMessage(save.error)}</InlineNotice>}
        {unassign.error && <InlineNotice tone="danger">{errorMessage(unassign.error)}</InlineNotice>}
        {save.isSuccess && <InlineNotice tone="success"><Check /> Assignment saved.</InlineNotice>}
      </div>
      <footer className="workflow-footer">
        <button type="button" className="button secondary" onClick={onClose}>Cancel</button>
        {hasAssignment && (
          <button
            type="button"
            className="button danger"
            disabled={save.isPending || unassign.isPending || reason.trim().length < 3}
            onClick={() => {
              if (confirm(
                `Unassign ${sensor.name} from its circuit, electric service, Home total, and monitoring groups? Historical readings will remain unchanged.`,
              )) unassign.mutate()
            }}
          >
            {unassign.isPending ? 'Unassigning…' : 'Unassign sensor'}
          </button>
        )}
        <button
          type="button"
          className="button primary"
          disabled={!canSubmit || save.isPending || unassign.isPending || circuits.isLoading}
          onClick={() => { save.mutate() }}
        >
          {save.isPending ? 'Saving…' : 'Save assignment'}
        </button>
      </footer>
    </section>
  )
}
