import { useMutation, useQuery } from '@tanstack/react-query'
import { Archive, CalendarRange, CheckCircle2, Download, FileClock, HardDrive, LoaderCircle } from 'lucide-react'
import { useMemo, useState, type FormEvent } from 'react'
import { api, ApiError } from '../api'
import type { ApiProblem } from '../types'
import { EmptyState, ErrorState, formatTime, LoadingState, Panel } from './UI'

interface LogService {
  id: string
  available: boolean
  stored_size_bytes: number
}

interface LogAvailability {
  earliest_date?: string
  latest_date?: string
  retention_days: number
  stored_size_bytes: number
  last_rotation_at?: string
  services: LogService[]
}

interface LogExportJob {
  id: string
  status: string
  start_date: string
  end_date: string
  services: string[]
  size_bytes?: number
  download_url?: string
}

const serviceLabels: Record<string, string> = {
  api: 'API and backend',
  worker: 'Worker and scheduled jobs',
  enrollment: 'Device enrollment',
  device_sync: 'Device synchronization',
  rate_sync: 'Rate synchronization',
  backup: 'Backup and restore',
}

const isoDate = (date: Date) => date.toISOString().slice(0, 10)
const daysBefore = (days: number) => {
  const date = new Date()
  date.setUTCDate(date.getUTCDate() - days)
  return isoDate(date)
}
const formatBytes = (value: number) => {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MiB`
  return `${(value / 1024 / 1024 / 1024).toFixed(1)} GiB`
}

async function downloadArchive(job: LogExportJob): Promise<void> {
  if (!job.download_url) throw new Error('The server did not provide a download URL.')
  const response = await fetch(job.download_url, { credentials: 'same-origin' })
  if (!response.ok) {
    const problem = await response.json().catch(() => ({
      title: 'Download failed',
      detail: `The server returned ${response.status}.`,
      status: response.status,
      code: 'download_failed',
    })) as ApiProblem
    throw new ApiError(problem)
  }
  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = `power-monitor-logs_${job.start_date}_to_${job.end_date}.zip`
  document.body.append(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(objectUrl)
}

export function ApplicationLogs() {
  const [startDate, setStartDate] = useState(daysBefore(6))
  const [endDate, setEndDate] = useState(daysBefore(0))
  const [service, setService] = useState('all')
  const [success, setSuccess] = useState<string>()
  const availability = useQuery({
    queryKey: ['log-availability'],
    queryFn: () => api<LogAvailability>('/api/v1/admin/logs/availability'),
  })
  const earliestSelectable = useMemo(() => {
    const boundary = daysBefore(89)
    return availability.data?.earliest_date && availability.data.earliest_date > boundary
      ? availability.data.earliest_date
      : boundary
  }, [availability.data?.earliest_date])
  const latestSelectable = availability.data?.latest_date && availability.data.latest_date < daysBefore(0)
    ? availability.data.latest_date
    : daysBefore(0)
  const prepare = useMutation({
    mutationFn: async () => {
      const services = service === 'all' ? availability.data?.services.map((item) => item.id) : [service]
      const job = await api<LogExportJob>('/api/v1/admin/logs/exports', {
        method: 'POST',
        body: JSON.stringify({ start_date: startDate, end_date: endDate, services }),
      })
      await downloadArchive(job)
      return job
    },
    onSuccess: () => { setSuccess('Log export is ready.') },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    setSuccess(undefined)
    prepare.reset()
    prepare.mutate()
  }
  const problem = prepare.error instanceof ApiError ? prepare.error.problem : undefined

  return (
    <Panel title="Application logs" eyebrow="Downloadable · 90-day retention" className="application-logs">
      {availability.isLoading ? <LoadingState label="Checking application-log availability…" /> : availability.error ? (
        <ErrorState error={availability.error} retry={() => { void availability.refetch() }} />
      ) : !availability.data?.earliest_date ? (
        <EmptyState title="No application logs available" message="Logs will appear here after the API, worker, enrollment, synchronization, or backup services write their first durable entry." />
      ) : (
        <>
          <dl className="log-availability-grid">
            <div><dt><CalendarRange /> Available range</dt><dd>{availability.data.earliest_date} to {availability.data.latest_date}</dd></div>
            <div><dt><FileClock /> Retention</dt><dd>{availability.data.retention_days} days</dd></div>
            <div><dt><HardDrive /> Stored size</dt><dd>{formatBytes(availability.data.stored_size_bytes)}</dd></div>
            <div><dt><Archive /> Last rotation</dt><dd>{availability.data.last_rotation_at ? formatTime(availability.data.last_rotation_at) : 'Not rotated yet'}</dd></div>
          </dl>
          <form className="log-export-form" onSubmit={submit}>
            <div className="form-columns">
              <label><span>Start date</span><input type="date" min={earliestSelectable} max={latestSelectable} value={startDate} onChange={(event) => { setStartDate(event.target.value) }} required /></label>
              <label><span>End date</span><input type="date" min={earliestSelectable} max={latestSelectable} value={endDate} onChange={(event) => { setEndDate(event.target.value) }} required /></label>
            </div>
            <label><span>Service or category</span><select value={service} onChange={(event) => { setService(event.target.value) }}><option value="all">All application services</option>{availability.data.services.map((item) => <option key={item.id} value={item.id}>{serviceLabels[item.id] ?? item.id}{item.available ? ` · ${formatBytes(item.stored_size_bytes)}` : ' · no logs'}</option>)}</select></label>
            {startDate > endDate && <p className="field-error" role="alert">End date must be on or after the start date.</p>}
            {problem && <div className="form-error" role="alert"><strong>{problem.title}</strong><span>{problem.detail}</span></div>}
            {prepare.isPending && <div className="log-export-progress" role="status"><LoaderCircle className="spin" /><span>Preparing and securely redacting the export…</span></div>}
            {success && <div className="form-success" role="status"><CheckCircle2 /> {success}</div>}
            <button className="button primary" disabled={prepare.isPending || startDate > endDate}><Download size={17} />{prepare.isPending ? 'Preparing export…' : 'Download logs'}</button>
          </form>
        </>
      )}
    </Panel>
  )
}
