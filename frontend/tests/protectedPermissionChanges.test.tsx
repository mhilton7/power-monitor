import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api/client'
import { RoleEditor } from '../src/pages/settings/SettingsPage'
import type { PermissionOption } from '../src/types/models'

const { requestMock } = vi.hoisted(() => ({
  requestMock: vi.fn<(path: string, init?: RequestInit, adapt?: (value: unknown) => unknown) => Promise<unknown>>(),
}))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, request: requestMock }
})

const permissions: PermissionOption[] = [
  { code: 'overview.view', group: 'Dashboard', label: 'View overview', description: 'View household summaries.', highRisk: false },
  { code: 'users.view', group: 'Access', label: 'View users', description: 'View local users.', highRisk: false },
  { code: 'users.manage', group: 'Access', label: 'Manage users', description: 'Change local user access.', highRisk: true },
]

function renderEditor({ mfaEnabled = false, onSaved = vi.fn() } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const view = render(
    <QueryClientProvider client={client}>
      <RoleEditor mode="create" permissions={permissions} mfaEnabled={mfaEnabled} onClose={vi.fn()} onSaved={onSaved} />
    </QueryClientProvider>,
  )
  return { ...view, onSaved }
}

async function completeRoleForm(permissionName: string) {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Role name'), 'Household manager')
  await user.type(screen.getByLabelText('Description'), 'Manages selected household settings')
  await user.click(screen.getByRole('checkbox', { name: new RegExp(permissionName, 'i') }))
  await user.click(screen.getByRole('button', { name: 'Save role' }))
  return user
}

function requestPayload(index: number): Record<string, unknown> {
  const body = requestMock.mock.calls[index]?.[1]?.body
  if (typeof body !== 'string') throw new TypeError('expected a JSON request body')
  return JSON.parse(body) as Record<string, unknown>
}

describe('protected permission changes', () => {
  beforeEach(() => { requestMock.mockReset() })

  it('opens confirmation before sending a high-risk permission save', async () => {
    renderEditor()
    await completeRoleForm('Manage users')

    expect(screen.getByRole('heading', { name: 'Confirm this protected change' })).toBeInTheDocument()
    expect(screen.getByLabelText('Current password')).toHaveAttribute('autocomplete', 'current-password')
    expect(requestMock).not.toHaveBeenCalled()
  })

  it('reauthenticates with the current password and then saves', async () => {
    requestMock.mockResolvedValue({})
    const onSaved = vi.fn()
    renderEditor({ onSaved })
    const user = await completeRoleForm('Manage users')
    await user.type(screen.getByLabelText('Current password'), 'correct-current-password')
    await user.click(screen.getByRole('button', { name: 'Confirm and save' }))

    await waitFor(() => { expect(onSaved).toHaveBeenCalledOnce() })
    expect(requestMock).toHaveBeenNthCalledWith(1, '/api/v1/auth/reauthenticate', expect.objectContaining({ method: 'POST' }))
    expect(requestPayload(0)).toEqual({ password: 'correct-current-password' })
    expect(requestMock).toHaveBeenNthCalledWith(2, '/api/v1/admin/roles', expect.objectContaining({ method: 'POST' }))
    expect(requestPayload(1)).toMatchObject({ confirm_high_risk: true, permissions: ['users.manage'] })
  })

  it('reauthenticates with an MFA code when MFA is enabled', async () => {
    requestMock.mockResolvedValue({})
    const onSaved = vi.fn()
    renderEditor({ mfaEnabled: true, onSaved })
    const user = await completeRoleForm('Manage users')
    expect(screen.getByText(/either your current password or a six-digit MFA code/i)).toBeInTheDocument()
    await user.type(screen.getByLabelText('MFA code'), '123456')
    await user.click(screen.getByRole('button', { name: 'Confirm and save' }))

    await waitFor(() => { expect(onSaved).toHaveBeenCalledOnce() })
    expect(requestPayload(0)).toEqual({ totp_code: '123456' })
  })

  it('shows an inline error and does not save after invalid confirmation', async () => {
    requestMock.mockRejectedValueOnce(new ApiError({ title: 'Reauthentication failed', detail: 'The current password or MFA code was not accepted', status: 401, code: 'reauthentication_failed' }))
    renderEditor()
    const user = await completeRoleForm('Manage users')
    await user.type(screen.getByLabelText('Current password'), 'wrong-password')
    await user.click(screen.getByRole('button', { name: 'Confirm and save' }))

    expect(await screen.findByText('The current password or MFA code was not accepted')).toBeInTheDocument()
    expect(requestMock).toHaveBeenCalledOnce()
  })

  it('cancels without saving and preserves the role draft', async () => {
    renderEditor()
    const user = await completeRoleForm('Manage users')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.getByRole('heading', { name: 'New custom role' })).toBeInTheDocument()
    expect(screen.getByLabelText('Role name')).toHaveValue('Household manager')
    expect(requestMock).not.toHaveBeenCalled()
  })

  it('saves an ordinary permission without step-up confirmation', async () => {
    requestMock.mockResolvedValue({})
    const onSaved = vi.fn()
    renderEditor({ onSaved })
    await completeRoleForm('View overview')

    await waitFor(() => { expect(onSaved).toHaveBeenCalledOnce() })
    expect(screen.queryByRole('heading', { name: 'Confirm this protected change' })).not.toBeInTheDocument()
    expect(requestMock).toHaveBeenCalledOnce()
    expect(requestPayload(0)).toMatchObject({ confirm_high_risk: false, permissions: ['overview.view'] })
  })
})
