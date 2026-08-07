import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

const requestMock = vi.hoisted(() => vi.fn())

vi.mock('../src/api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../src/api/client')>()
  return { ...original, request: requestMock }
})

vi.mock('../src/state/AuthContext', () => ({
  useAuth: () => ({
    session: {
      authenticated: true,
      bootstrapRequired: false,
      user: {
        id: 'owner-1',
        email: 'owner@example.test',
        name: 'Owner',
        roles: ['owner'],
        permissions: ['topology.manage'],
        allHomes: true,
        homeIds: [],
        accessRevision: 1,
        mfaEnabled: false,
      },
    },
    loading: false,
    error: null,
    refresh: vi.fn(),
  }),
}))

vi.mock('../src/state/LiveHomeContext', () => ({
  useLiveHome: () => ({
    sensors: [{
      id: 'sensor-1',
      name: 'Indoor AC',
      circuitId: 'used-circuit',
    }],
  }),
}))

import { BrowserRouter } from '../src/app/router'
import { TopologyDetail } from '../src/pages/settings/SettingsPage'

const circuits = [
  {
    id: 'unused-circuit',
    site_id: 'home-1',
    parent_id: null,
    name: 'Unused branch',
    measurement_role: 'branch',
    split_phase_group: null,
  },
  {
    id: 'used-circuit',
    site_id: 'home-1',
    parent_id: null,
    name: 'Used branch',
    measurement_role: 'branch',
    split_phase_group: null,
  },
]

function renderTopology() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  return render(
    <BrowserRouter>
      <QueryClientProvider client={client}>
        <TopologyDetail homeId="home-1" />
      </QueryClientProvider>
    </BrowserRouter>,
  )
}

function managementRow(name: string) {
  const management = screen.getByRole('heading', { name: 'Topology management' }).closest('section')
  if (!management) throw new Error('Topology management surface is missing')
  const row = within(management).getByText(name).closest('.list-row')
  if (!row) throw new Error(`Management row for ${name} is missing`)
  return row as HTMLElement
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('topology removal controls', () => {
  it('removes an unused circuit only after exact confirmation and preserves history', async () => {
    requestMock.mockImplementation((path: string, options?: RequestInit, adapter?: (value: unknown) => unknown) => {
      let value: unknown
      if (path.startsWith('/api/v1/circuits?')) value = circuits
      else if (path.startsWith('/api/v1/aggregate-sets?')) value = []
      else if (path === '/api/v1/circuits/unused-circuit' && options?.method === 'DELETE') value = undefined
      else throw new Error(`Unexpected request: ${path}`)
      return Promise.resolve(adapter ? adapter(value) : value)
    })

    const user = userEvent.setup()
    renderTopology()
    await user.click(within(await waitFor(() => managementRow('Unused branch'))).getByRole('button', { name: 'Remove' }))

    expect(screen.getByText('Historical readings preserved').parentElement).toHaveTextContent('Yes')
    const remove = screen.getByRole('button', { name: 'Remove circuit' })
    expect(remove).toBeDisabled()
    await user.type(screen.getByLabelText('Type REMOVE CIRCUIT Unused branch'), 'REMOVE CIRCUIT Unused branch')
    expect(remove).toBeEnabled()
    await user.click(remove)

    await waitFor(() => { expect(screen.queryByRole('dialog')).not.toBeInTheDocument() })
    expect(requestMock).toHaveBeenCalledWith(
      '/api/v1/circuits/unused-circuit',
      { method: 'DELETE' },
    )
  })

  it('shows sensor dependencies and the server conflict without deleting anything else', async () => {
    requestMock.mockImplementation((path: string, options?: RequestInit, adapter?: (value: unknown) => unknown) => {
      let value: unknown
      if (path.startsWith('/api/v1/circuits?')) value = circuits
      else if (path.startsWith('/api/v1/aggregate-sets?')) value = []
      else if (path === '/api/v1/circuits/used-circuit' && options?.method === 'DELETE') {
        return Promise.reject(new Error('Circuit cannot be removed because Indoor AC is assigned to it.'))
      } else throw new Error(`Unexpected request: ${path}`)
      return Promise.resolve(adapter ? adapter(value) : value)
    })

    const user = userEvent.setup()
    renderTopology()
    await user.click(within(await waitFor(() => managementRow('Used branch'))).getByRole('button', { name: 'Remove' }))

    expect(screen.getByText('Assigned sensors').parentElement).toHaveTextContent('Indoor AC')
    expect(screen.getByText(/Repair the listed dependencies/)).toBeVisible()
    await user.type(screen.getByLabelText('Type REMOVE CIRCUIT Used branch'), 'REMOVE CIRCUIT Used branch')
    await user.click(screen.getByRole('button', { name: 'Remove circuit' }))

    expect(await screen.findByText('Circuit cannot be removed because Indoor AC is assigned to it.')).toBeVisible()
    expect(screen.getByRole('dialog')).toBeVisible()
    expect(requestMock).toHaveBeenCalledWith(
      '/api/v1/circuits/used-circuit',
      { method: 'DELETE' },
    )
  })
})
