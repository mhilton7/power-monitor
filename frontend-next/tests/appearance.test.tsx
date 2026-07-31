import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const requestMock = vi.hoisted(() => vi.fn())
vi.mock('../src/api/client', () => ({ request: requestMock }))
vi.mock('../src/state/AuthContext', () => ({
  useAuth: () => ({ session: { authenticated: true, user: { id: 'owner' } } }),
}))

import { AppearanceProvider, useAppearance } from '../src/state/AppearanceContext'

function Harness() {
  const appearance = useAppearance()
  return <>
    <output aria-label="power color">{appearance.chartColors.power}</output>
    <output aria-label="energy color">{appearance.chartColors.energy}</output>
    <output aria-label="cost color">{appearance.chartColors.cost}</output>
    <output aria-label="revision">{appearance.chartColorRevision}</output>
    <button type="button" onClick={() => appearance.publishChartColors({
      power: '#123456', energy: '#345678', cost: '#ABCDEF',
    })}>Apply shared colors</button>
  </>
}

describe('administrator-published chart colors', () => {
  beforeEach(() => {
    window.localStorage.clear()
    requestMock.mockReset()
    requestMock.mockImplementation((_path: string, init: RequestInit = {}, adapt?: (value: unknown) => unknown) => {
      const response = init.method === 'PUT'
        ? { chart_power_color: '#123456', chart_energy_color: '#345678', chart_cost_color: '#ABCDEF', revision: 8, updated_at: '2026-07-31T20:00:00Z' }
        : { chart_power_color: '#112233', chart_energy_color: '#445566', chart_cost_color: '#778899', revision: 7, updated_at: '2026-07-31T19:00:00Z' }
      return Promise.resolve(adapt ? adapt(response) : response)
    })
  })

  it('loads one server-published palette and applies a revision-checked update', async () => {
    const user = userEvent.setup()
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AppearanceProvider><Harness /></AppearanceProvider></QueryClientProvider>)

    await waitFor(() => { expect(screen.getByLabelText('power color')).toHaveTextContent('#112233') })
    expect(screen.getByLabelText('cost color')).toHaveTextContent('#778899')
    expect(screen.getByLabelText('revision')).toHaveTextContent('7')
    await user.click(screen.getByRole('button', { name: 'Apply shared colors' }))
    await waitFor(() => { expect(screen.getByLabelText('power color')).toHaveTextContent('#123456') })
    expect(screen.getByLabelText('revision')).toHaveTextContent('8')
    const calls = requestMock.mock.calls as unknown[][]
    const put = calls.find((call) => (call[1] as RequestInit | undefined)?.method === 'PUT')
    const putInit = put?.[1] as RequestInit | undefined
    expect(typeof putInit?.body).toBe('string')
    if (typeof putInit?.body !== 'string') throw new Error('Expected a JSON request body')
    expect(JSON.parse(putInit.body)).toMatchObject({
      chart_power_color: '#123456',
      chart_energy_color: '#345678',
      chart_cost_color: '#ABCDEF',
      expected_revision: 7,
    })
    expect(window.localStorage.getItem('pm-chart-power-color')).toBeNull()
  })
})
