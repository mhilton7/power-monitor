import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthPage } from '../src/pages/AuthPage'

afterEach(cleanup)

const authenticatedSession = {
  authenticated: true,
  bootstrap_required: false,
  user: { id: 'user-1', email: 'autofill@example.test', display_name: 'Autofill Test', roles: ['admin'] },
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json' },
  })
}

function requestPath(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.pathname
  return new URL(input.url).pathname
}

function renderAuth(bootstrapRequired = false) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/sign-in']}>
        <Routes>
          <Route path="/sign-in" element={<AuthPage bootstrapRequired={bootstrapRequired} />} />
          <Route path="/" element={<div>Authenticated dashboard</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function setNativeValue(input: HTMLInputElement, value: string) {
  input.value = value
}

function requestBodyText(init?: RequestInit): string {
  return typeof init?.body === 'string' ? init.body : ''
}

function mockSuccessfulApi(onLogin?: (init?: RequestInit) => void) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
    const path = requestPath(input)
    if (path === '/api/v1/public/interface-text') return Promise.resolve(jsonResponse({ revision: 0, values: {} }))
    if (path === '/api/v1/auth/login') {
      onLogin?.(init)
      return Promise.resolve(jsonResponse(authenticatedSession))
    }
    if (path === '/api/v1/auth/bootstrap') {
      onLogin?.(init)
      return Promise.resolve(jsonResponse(authenticatedSession))
    }
    return Promise.reject(new Error(`Unexpected request: ${path}`))
  })
}

describe('login password-manager contract', () => {
  it('renders one semantic form with stable native credential attributes', async () => {
    mockSuccessfulApi()
    const { container } = renderAuth()
    await screen.findByRole('heading', { name: 'Sign in to your dashboard' })

    const forms = container.querySelectorAll('form')
    const form = container.querySelector<HTMLFormElement>('#login-form')
    const username = screen.getByLabelText<HTMLInputElement>('Email address')
    const password = screen.getByLabelText<HTMLInputElement>('Password')
    const totp = screen.getByLabelText<HTMLInputElement>(/TOTP code/)

    expect(forms).toHaveLength(1)
    expect(form).not.toBeNull()
    expect(form).toContainElement(username)
    expect(form).toContainElement(password)
    expect(form).toHaveAttribute('method', 'post')
    expect(form).toHaveAttribute('autocomplete', 'on')
    expect(username).toHaveAttribute('id', 'login-username')
    expect(username).toHaveAttribute('name', 'username')
    expect(username).toHaveAttribute('type', 'email')
    expect(username).toHaveAttribute('inputmode', 'email')
    expect(username).toHaveAttribute('autocomplete', 'username')
    expect(username).toHaveAttribute('autocapitalize', 'none')
    expect(username).toHaveAttribute('spellcheck', 'false')
    expect(username.labels).toHaveLength(1)
    expect(password).toHaveAttribute('id', 'current-password')
    expect(password).toHaveAttribute('name', 'password')
    expect(password).toHaveAttribute('type', 'password')
    expect(password).toHaveAttribute('autocomplete', 'current-password')
    expect(password.labels).toHaveLength(1)
    expect(totp).toHaveAttribute('autocomplete', 'one-time-code')
    expect(screen.getByRole('button', { name: 'Sign in' })).toHaveAttribute('type', 'submit')
    expect(form?.querySelectorAll('input[name="username"]')).toHaveLength(1)
    expect(form?.querySelectorAll('input[name="password"]')).toHaveLength(1)
    expect(form?.querySelectorAll('input[type="hidden"]')).toHaveLength(0)
  })

  it('submits exact native DOM values without React change events', async () => {
    let requestBody = ''
    mockSuccessfulApi((init) => { requestBody = requestBodyText(init) })
    renderAuth()
    const username = await screen.findByLabelText<HTMLInputElement>('Email address')
    const password = screen.getByLabelText<HTMLInputElement>('Password')
    const totp = screen.getByLabelText<HTMLInputElement>(/TOTP code/)

    setNativeValue(username, 'native.autofill@example.test')
    setNativeValue(password, '  Exact Password 42!  ')
    setNativeValue(totp, '123456')
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await screen.findByText('Authenticated dashboard')
    expect(JSON.parse(requestBody)).toEqual({
      email: 'native.autofill@example.test',
      password: '  Exact Password 42!  ',
      totp_code: '123456',
    })
  })

  it('submits browser input events without depending on React change state', async () => {
    let requestBody = ''
    mockSuccessfulApi((init) => { requestBody = requestBodyText(init) })
    renderAuth()
    const username = await screen.findByLabelText<HTMLInputElement>('Email address')
    const password = screen.getByLabelText<HTMLInputElement>('Password')
    fireEvent.input(username, { target: { value: 'input-event@example.test' } })
    fireEvent.input(password, { target: { value: 'Input Event Password 42!' } })
    fireEvent.submit(document.querySelector('#login-form') as HTMLFormElement)

    await screen.findByText('Authenticated dashboard')
    expect(JSON.parse(requestBody)).toMatchObject({
      email: 'input-event@example.test',
      password: 'Input Event Password 42!',
    })
  })

  it('preserves input nodes and native values while published interface text loads', async () => {
    let resolveText: ((response: Response) => void) | undefined
    const pendingText = new Promise<Response>((resolve) => { resolveText = resolve })
    let requestBody = ''
    vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      const path = requestPath(input)
      if (path === '/api/v1/public/interface-text') return pendingText
      if (path === '/api/v1/auth/login') {
        requestBody = requestBodyText(init)
        return Promise.resolve(jsonResponse(authenticatedSession))
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    renderAuth()
    const originalUsername = screen.getByLabelText<HTMLInputElement>('Email address')
    const originalPassword = screen.getByLabelText<HTMLInputElement>('Password')
    setNativeValue(originalUsername, 'preserved@example.test')
    setNativeValue(originalPassword, 'Preserved Password 42!')

    await act(async () => {
      resolveText?.(jsonResponse({
        revision: 7,
        values: {
          'login.heading': 'Customized secure access',
          'login.email_label': 'Account email',
          'login.password_label': 'Account password',
        },
      }))
      await pendingText
    })
    await screen.findByRole('heading', { name: 'Customized secure access' })

    const updatedUsername = screen.getByLabelText<HTMLInputElement>('Account email')
    const updatedPassword = screen.getByLabelText<HTMLInputElement>('Account password')
    expect(updatedUsername).toBe(originalUsername)
    expect(updatedPassword).toBe(originalPassword)
    expect(updatedUsername).toHaveValue('preserved@example.test')
    expect(updatedPassword).toHaveValue('Preserved Password 42!')
    expect(updatedUsername).toHaveAttribute('id', 'login-username')
    expect(updatedUsername).toHaveAttribute('name', 'username')
    expect(updatedPassword).toHaveAttribute('autocomplete', 'current-password')

    fireEvent.submit(document.querySelector('#login-form') as HTMLFormElement)
    await screen.findByText('Authenticated dashboard')
    expect(JSON.parse(requestBody)).toMatchObject({
      email: 'preserved@example.test',
      password: 'Preserved Password 42!',
    })
  })

  it('keeps the default form usable when public interface text fails', async () => {
    let loginCalled = false
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const path = requestPath(input)
      if (path === '/api/v1/public/interface-text') return Promise.resolve(jsonResponse({ title: 'Unavailable', detail: 'Text unavailable', status: 503, code: 'unavailable' }, 503))
      if (path === '/api/v1/auth/login') {
        loginCalled = true
        return Promise.resolve(jsonResponse(authenticatedSession))
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    renderAuth()
    const username = await screen.findByLabelText<HTMLInputElement>('Email address')
    const password = screen.getByLabelText<HTMLInputElement>('Password')
    setNativeValue(username, 'fallback@example.test')
    setNativeValue(password, 'Fallback Password 42!')
    fireEvent.submit(document.querySelector('#login-form') as HTMLFormElement)

    await screen.findByText('Authenticated dashboard')
    expect(loginCalled).toBe(true)
  })

  it('leaves failed credentials in the same usable form and associates the error', async () => {
    const user = userEvent.setup()
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const path = requestPath(input)
      if (path === '/api/v1/public/interface-text') return Promise.resolve(jsonResponse({ revision: 0, values: {} }))
      if (path === '/api/v1/auth/login') return Promise.resolve(jsonResponse({ title: 'Sign-in failed', detail: 'The supplied credentials are invalid.', status: 401, code: 'invalid_credentials' }, 401))
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    const { container } = renderAuth()
    const form = container.querySelector('#login-form')
    const username = await screen.findByLabelText<HTMLInputElement>('Email address')
    const password = screen.getByLabelText<HTMLInputElement>('Password')
    await user.type(username, 'failed@example.test')
    await user.type(password, 'Failed Password 42!')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    await screen.findByRole('alert')
    expect(container.querySelector('#login-form')).toBe(form)
    expect(username).toHaveValue('failed@example.test')
    expect(password).toHaveValue('Failed Password 42!')
    expect(username).toHaveAttribute('aria-describedby', 'authentication-error')
    expect(password).toHaveAttribute('aria-describedby', 'authentication-error')
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled()
  })

  it('toggles password visibility on the same node without changing its value or purpose', async () => {
    const user = userEvent.setup()
    mockSuccessfulApi()
    renderAuth()
    const password = await screen.findByLabelText<HTMLInputElement>('Password')
    setNativeValue(password, 'Visible Password 42!')
    password.focus()
    password.setSelectionRange(2, 8, 'forward')

    await user.click(screen.getByRole('button', { name: 'Show password' }))
    const visiblePassword = screen.getByLabelText<HTMLInputElement>('Password')
    expect(visiblePassword).toBe(password)
    expect(visiblePassword).toHaveAttribute('type', 'text')
    expect(visiblePassword).toHaveAttribute('name', 'password')
    expect(visiblePassword).toHaveAttribute('autocomplete', 'current-password')
    expect(visiblePassword).toHaveValue('Visible Password 42!')
    expect(visiblePassword.selectionStart).toBe(2)
    expect(visiblePassword.selectionEnd).toBe(8)
    expect(screen.getByRole('button', { name: 'Hide password' })).toHaveAttribute('type', 'button')

    await user.click(screen.getByRole('button', { name: 'Hide password' }))
    expect(screen.getByLabelText('Password')).toBe(password)
    expect(password).toHaveAttribute('type', 'password')
    expect(password).toHaveValue('Visible Password 42!')
  })

  it('keeps first-run account creation distinct from current-password sign-in', async () => {
    mockSuccessfulApi()
    const { container } = renderAuth(true)
    await screen.findByRole('heading', { name: 'Create the administrator' })
    const form = container.querySelector('#bootstrap-form')
    const email = screen.getByLabelText('Email address')
    const password = screen.getByLabelText('Password')
    const setupToken = screen.getByLabelText('One-time bootstrap secret')
    expect(form).toHaveAttribute('method', 'post')
    expect(email).toHaveAttribute('name', 'email')
    expect(email).toHaveAttribute('autocomplete', 'username')
    expect(password).toHaveAttribute('id', 'new-password')
    expect(password).toHaveAttribute('autocomplete', 'new-password')
    expect(setupToken).toHaveAttribute('autocomplete', 'one-time-code')
  })
})
