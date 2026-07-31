import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AlertSummary } from '../src/types/models'

let permissions = ['alerts.view', 'alerts.acknowledge', 'alerts.manage_delivery', 'devices.view']
const navigateMock = vi.hoisted(() => vi.fn())

vi.mock('../src/state/AuthContext', () => ({
  useAuth: () => ({
    session: {
      authenticated: true,
      user: { id: 'owner', permissions, roles: ['admin'] },
    },
  }),
}))

vi.mock('../src/app/router', () => ({ useNavigate: () => navigateMock }))
vi.mock('../src/api/client', () => ({ request: vi.fn(() => Promise.resolve({})) }))

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
  suppression: { dismissible: false, permanentlySuppressible: false, currentlySuppressed: false, allowedScopes: [] },
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

function renderDrawer(alerts = [heartbeat, recommendation]) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <AlertDrawer open alerts={alerts} onClose={vi.fn()} />
    </QueryClientProvider>,
  )
}

describe('detailed notification center', () => {
  beforeEach(() => {
    permissions = ['alerts.view', 'alerts.acknowledge', 'alerts.manage_delivery', 'devices.view']
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
})
