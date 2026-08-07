import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AlertSummary } from '../src/types/models'

let permissions = ['alerts.view', 'alerts.acknowledge', 'alerts.manage_delivery', 'devices.view']
const navigateMock = vi.hoisted(() => vi.fn())
const requestMock = vi.hoisted(() => vi.fn(() => Promise.resolve({})))

vi.mock('../src/state/AuthContext', () => ({
  useAuth: () => ({
    session: {
      authenticated: true,
      user: { id: 'owner', permissions, roles: ['admin'] },
    },
  }),
}))

vi.mock('../src/app/router', () => ({ useNavigate: () => navigateMock }))
vi.mock('../src/api/client', () => ({ request: requestMock }))

import { AlertDrawer } from '../src/features/alerts/AlertDrawer'

const heartbeat: AlertSummary = {
  id: 'alert-1',
  code: 'heartbeat_stale',
  kind: 'operational_alert',
  category: 'connectivity',
  title: 'Indoor-AC stopped reporting',
  message: 'No signed heartbeat has been received for 92 seconds.',
  severity: 'error',
  status: 'open',
  openedAt: '2026-07-31T16:00:00Z',
  lastSeenAt: '2026-07-31T16:01:32Z',
  occurrenceCount: 3,
  durationSeconds: 92,
  affectedResource: { type: 'sensor', id: 'sensor-1', name: 'Indoor-AC' },
  observed: { label: 'Heartbeat age', value: '92', unit: 'seconds' },
  expected: { label: 'Configured threshold', operator: 'within', value: '60', unit: 'seconds' },
  cause: { code: 'heartbeat_stale', explanation: 'The signed heartbeat window expired.' },
  evidence: [{ label: 'Last known power', value: '842.6 W', status: 'warning' }],
  impact: 'Live values may be stale while retained readings wait on the sensor.',
  remediation: {
    summary: 'Check sensor power and network access.',
    steps: ['Confirm the sensor has power.', 'Review Wi-Fi signal and DNS.'],
    automaticRecovery: 'The sensor retries automatically after connectivity returns.',
    action: { label: 'Open sensor details', target: '/settings/sensors', requiredPermissions: ['devices.view'] },
  },
  delivery: { attempted: true, channelName: 'Home email', lastOutcome: 'retry_scheduled', safeErrorCode: 'smtp_starttls_failed', safeErrorSummary: 'STARTTLS negotiation failed' },
  suppression: { dismissible: true, permanentlySuppressible: false, currentlySuppressed: false, allowedScopes: [] },
}

const recommendation: AlertSummary = {
  id: 'recommendation:recommendation.smtp_not_configured:home-1',
  code: 'recommendation.smtp_not_configured',
  kind: 'setup_recommendation',
  category: 'delivery',
  title: 'Email notifications are not configured',
  message: 'Dashboard alerts continue, but Power Monitor cannot send email.',
  severity: 'info',
  status: 'open',
  openedAt: '2026-07-31T16:00:00Z',
  lastSeenAt: '2026-07-31T16:01:32Z',
  occurrenceCount: 1,
  affectedResource: { type: 'home', id: 'home-1', name: 'Home' },
  evidence: [],
  impact: 'Only optional email delivery is unavailable.',
  remediation: { summary: 'Set up email if wanted.', steps: ['Add an SMTP channel.'] },
  suppression: { dismissible: true, permanentlySuppressible: true, suppressionKey: 'recommendation.smtp_not_configured', currentlySuppressed: false, allowedScopes: ['user', 'home'] },
}

const resolvedHeartbeat: AlertSummary = {
  ...heartbeat,
  id: 'resolved-alert-1',
  status: 'resolved',
  resolvedAt: '2026-07-31T16:02:00Z',
}

function renderDrawer(alerts = [heartbeat, recommendation]) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <AlertDrawer open alerts={alerts} onClose={vi.fn()} />
    </QueryClientProvider>,
  )
}

function QueryBackedDrawer({ alerts }: { alerts: AlertSummary[] }) {
  const query = useQuery({
    queryKey: ['alerts', 'owner', 'active'],
    queryFn: () => Promise.resolve({ items: alerts, total: alerts.length }),
    initialData: { items: alerts, total: alerts.length },
    enabled: false,
  })
  return <AlertDrawer open alerts={query.data.items} onClose={vi.fn()} />
}

function renderQueryBackedDrawer(alerts = [heartbeat]) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <QueryBackedDrawer alerts={alerts} />
    </QueryClientProvider>,
  )
}

describe('detailed notification center', () => {
  beforeEach(() => {
    permissions = ['alerts.view', 'alerts.acknowledge', 'alerts.manage_delivery', 'devices.view']
    requestMock.mockClear()
  })

  it('shows prioritized sections and detailed actionable evidence', async () => {
    const user = userEvent.setup()
    renderDrawer()
    expect(screen.getByRole('heading', { name: /Active issues/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Recommendations/ })).toBeInTheDocument()
    const summary = screen.getByRole('button', { name: /Indoor-AC stopped reporting/ })
    expect(summary).toHaveAttribute('aria-expanded', 'false')
    await user.click(summary)
    expect(summary).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('842.6 W')).toBeInTheDocument()
    expect(screen.getByText(/STARTTLS negotiation failed/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Acknowledge/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Silence/ })).toBeInTheDocument()
  })

  it('requires explicit confirmation before permanently ignoring SMTP setup', async () => {
    const user = userEvent.setup()
    renderDrawer()
    await user.click(screen.getByRole('button', { name: /Email notifications are not configured/ }))
    await user.click(screen.getByRole('button', { name: /Do not remind me again/ }))
    const dialog = screen.getByRole('dialog', { name: /Stop email setup reminders/ })
    expect(dialog).toBeInTheDocument()
    const submit = within(dialog).getByRole('button', { name: 'Do not remind me again' })
    expect(submit).toBeDisabled()
    await user.click(within(dialog).getByRole('checkbox'))
    expect(submit).toBeEnabled()
    expect(within(dialog).getAllByText(/dashboard alerts continue/i).length).toBeGreaterThan(0)
  })

  it('does not render lifecycle controls without effective permissions', async () => {
    permissions = ['alerts.view']
    const user = userEvent.setup()
    renderDrawer([heartbeat])
    await user.click(screen.getByRole('button', { name: /Indoor-AC stopped reporting/ }))
    expect(screen.queryByRole('button', { name: /Acknowledge/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Silence/ })).not.toBeInTheDocument()
  })

  it('shows acknowledgement immediately while the server request is pending', async () => {
    requestMock.mockImplementationOnce(() => new Promise(() => undefined))
    const user = userEvent.setup()
    renderQueryBackedDrawer()
    await user.click(screen.getByRole('button', { name: /Indoor-AC stopped reporting/ }))
    await user.click(screen.getByRole('button', { name: 'Acknowledge' }))

    await waitFor(() => {
      expect(screen.getByText(/Indoor-AC · Acknowledged/)).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Acknowledge' })).not.toBeInTheDocument()
    })
  })

  it('requires confirmation before removing a notification while preserving monitoring', async () => {
    const user = userEvent.setup()
    renderDrawer([heartbeat])
    await user.click(screen.getByRole('button', { name: /Indoor-AC stopped reporting/ }))
    await user.click(screen.getByRole('button', { name: 'Remove' }))
    const dialog = screen.getByRole('dialog', { name: /Remove this notification/ })
    expect(within(dialog).getByText(/Monitoring, alert rules, delivery history, and the audit record remain active/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Remove notification' }))
    await waitFor(() => {
      expect(requestMock).toHaveBeenCalledWith('/api/v1/notifications/alert-1/dismiss', { method: 'POST' })
    })
  })

  it('clears a recently resolved notification immediately while preserving rollback state', async () => {
    requestMock.mockImplementationOnce(() => new Promise(() => undefined))
    const user = userEvent.setup()
    renderQueryBackedDrawer([resolvedHeartbeat])
    await user.click(screen.getByRole('button', { name: /Indoor-AC stopped reporting/ }))
    await user.click(screen.getByRole('button', { name: 'Clear' }))
    const dialog = screen.getByRole('dialog', { name: /Clear this resolved notification/ })
    await user.click(within(dialog).getByRole('button', { name: 'Clear notification' }))

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /Indoor-AC stopped reporting/ })).not.toBeInTheDocument()
    })
    expect(requestMock).toHaveBeenCalledWith('/api/v1/notifications/resolved-alert-1/dismiss', { method: 'POST' })
  })

  it('clears every resolved occurrence with one confirmed action', async () => {
    const secondResolved = {
      ...resolvedHeartbeat,
      id: 'resolved-alert-2',
      lastSeenAt: '2026-07-31T15:02:00Z',
    }
    const user = userEvent.setup()
    renderQueryBackedDrawer([resolvedHeartbeat, secondResolved])

    expect(screen.getByRole('heading', { name: /Recently resolved 2/ })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Clear all' }))
    const dialog = screen.getByRole('dialog', { name: /Clear all resolved notifications/ })
    expect(within(dialog).getByText(/This clears/)).toHaveTextContent('This clears 2 resolved notifications')
    await user.click(within(dialog).getByRole('button', { name: 'Clear all resolved' }))

    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: /Recently resolved/ })).not.toBeInTheDocument()
    })
    expect(requestMock).toHaveBeenCalledWith('/api/v1/notifications/resolved-alert-1/dismiss', { method: 'POST' })
    expect(requestMock).toHaveBeenCalledWith('/api/v1/notifications/resolved-alert-2/dismiss', { method: 'POST' })
  })
})
