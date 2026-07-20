import { useQuery } from '@tanstack/react-query'
import { ChevronRight, Filter, RadioTower, Search, Wifi } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import type { Device } from '../types'
import { EmptyState, ErrorState, formatNumber, formatTime, LoadingState, PageTitle, Panel, StatusPill } from '../components/UI'

export function DevicesPage() {
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('all')
  const query = useQuery({ queryKey: ['devices'], queryFn: () => api<Device[]>('/api/v1/devices') })
  const rows = useMemo(() => (query.data ?? []).filter((device) =>
    device.name.toLowerCase().includes(search.toLowerCase()) && (status === 'all' || device.status === status),
  ), [query.data, search, status])
  return (
    <>
      <PageTitle eyebrow="Device operations" title="Sensor fleet" description="Permanent identities, mutable addresses, and evidence-backed health for every ESP32-S3 agent." />
      <Panel className="table-panel">
        <div className="table-toolbar">
          <label className="search-field"><Search size={17} /><input placeholder="Search devices" value={search} onChange={(event) => { setSearch(event.target.value); }} /></label>
          <label className="filter-field"><Filter size={16} /><span>Status</span><select value={status} onChange={(event) => { setStatus(event.target.value); }}><option value="all">All states</option><option value="online_synchronized">Synchronized</option><option value="online_with_backlog">Backlog</option><option value="offline_last_known">Offline</option><option value="revoked">Revoked</option></select></label>
        </div>
        {query.isLoading ? <LoadingState /> : query.error ? <ErrorState error={query.error} retry={() => void query.refetch()} /> : rows.length === 0 ? <EmptyState title="No matching devices" message="Adjust the filters or enroll a sensor." /> : (
          <div className="responsive-table">
            <table>
              <thead><tr><th>Device</th><th>Status</th><th>Live power</th><th>Subsystems</th><th>Connection</th><th>Last seen</th><th aria-label="Open" /></tr></thead>
              <tbody>{rows.map((device) => (
                <tr key={device.id}>
                  <td><Link className="device-cell" to={`/devices/${device.id}`}><span><RadioTower /></span><p><strong>{device.name}</strong><small>{device.measurement_role} · {device.ct_rating_amps} A CT</small></p></Link></td>
                  <td><StatusPill status={device.status} /></td>
                  <td><strong>{formatNumber(device.current_watts)} W</strong></td>
                  <td><div className="mini-indicators"><span className={device.pzem_ok ? 'ok' : 'bad'}>PZ</span><span className={device.sd_ok ? 'ok' : 'bad'}>SD</span><span className={device.time_trusted ? 'ok' : 'bad'}>TM</span><span className={device.backlog ? 'warn' : 'ok'}>SQ</span></div></td>
                  <td><span className="connection"><Wifi size={15} /> {device.connection_mode}{device.rssi_dbm ? ` · ${device.rssi_dbm} dBm` : ''}</span></td>
                  <td><time title={device.last_seen_at}>{formatTime(device.last_seen_at)}</time></td>
                  <td><Link className="icon-button" aria-label={`Open ${device.name}`} to={`/devices/${device.id}`}><ChevronRight /></Link></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  )
}

