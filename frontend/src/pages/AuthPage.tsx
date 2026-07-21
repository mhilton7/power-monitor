import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Activity, ArrowRight, Check, Eye, EyeOff, Server, ShieldCheck, Zap } from 'lucide-react'
import { useLayoutEffect, useRef, useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ApiError, api } from '../api'
import { usePublicInterfaceText } from '../interfaceText'
import type { Session } from '../types'

type LoginPayload = {
  email: string
  password: string
  totp_code?: string
}

type BootstrapPayload = {
  email: string
  display_name: string
  password: string
  bootstrap_secret: string
}

function formValue(data: FormData, name: string): string {
  const value = data.get(name)
  return typeof value === 'string' ? value : ''
}

export function AuthPage({ bootstrapRequired }: { bootstrapRequired: boolean }) {
  const { text } = usePublicInterfaceText()
  const [passwordVisible, setPasswordVisible] = useState(false)
  const passwordInput = useRef<HTMLInputElement>(null)
  const selectionToRestore = useRef<{ start: number | null; end: number | null; direction: 'forward' | 'backward' | 'none' | null } | null>(null)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()
  const destination = (location.state as { from?: string } | null)?.from ?? '/'
  const mutation = useMutation({
    mutationFn: (payload: LoginPayload | BootstrapPayload) => api<Session>(bootstrapRequired ? '/api/v1/auth/bootstrap' : '/api/v1/auth/login', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['session'] })
      await navigate(destination, { replace: true })
    },
  })
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const password = formValue(data, 'password')
    if (bootstrapRequired) {
      mutation.mutate({
        email: formValue(data, 'email'),
        display_name: formValue(data, 'display_name'),
        password,
        bootstrap_secret: formValue(data, 'bootstrap_secret'),
      })
      return
    }
    const totpCode = formValue(data, 'totp_code')
    mutation.mutate({
      email: formValue(data, 'username'),
      password,
      totp_code: totpCode || undefined,
    })
  }
  const problem = mutation.error instanceof ApiError ? mutation.error.problem : undefined
  const errorId = problem ? 'authentication-error' : undefined

  useLayoutEffect(() => {
    const selection = selectionToRestore.current
    const input = passwordInput.current
    if (!selection || !input) return
    const restoreSelection = () => {
      try {
        input.setSelectionRange(selection.start, selection.end, selection.direction ?? undefined)
      } catch {
        // Some browsers do not expose a selection for every password input state.
      }
    }
    restoreSelection()
    queueMicrotask(restoreSelection)
    if (typeof window.requestAnimationFrame !== 'function') {
      queueMicrotask(() => {
        if (selectionToRestore.current === selection) selectionToRestore.current = null
      })
      return
    }
    const frame = window.requestAnimationFrame(() => {
      restoreSelection()
      if (selectionToRestore.current === selection) selectionToRestore.current = null
    })
    return () => { window.cancelAnimationFrame(frame) }
  }, [passwordVisible])

  const rememberPasswordSelection = () => {
    const input = passwordInput.current
    if (input) {
      selectionToRestore.current = {
        start: input.selectionStart,
        end: input.selectionEnd,
        direction: input.selectionDirection,
      }
    }
  }

  const togglePasswordVisibility = () => {
    if (!selectionToRestore.current) rememberPasswordSelection()
    setPasswordVisible((visible) => !visible)
  }

  const formId = bootstrapRequired ? 'bootstrap-form' : 'login-form'
  const emailId = bootstrapRequired ? 'bootstrap-email' : 'login-username'
  const passwordId = bootstrapRequired ? 'new-password' : 'current-password'

  return (
    <main className="auth-page">
      <section className="auth-story">
        <div className="auth-brand"><span className="brand-mark"><Zap fill="currentColor" /></span><div><strong>{text('general.application_name')}</strong><small>{text('general.organization_tagline')}</small></div></div>
        <div className="auth-story-copy">
          <span className="eyebrow">Private fleet intelligence</span>
          <h1>Your energy.<br />Clearly measured.</h1>
          <p>Bring live ESP32 measurements, recovered microSD history, and Southern California Edison rate estimates into one private dashboard.</p>
          <div className="auth-proof">
            <span><Activity /><strong>Live</strong><small>fleet view</small></span>
            <span><Server /><strong>Local</strong><small>data custody</small></span>
            <span><ShieldCheck /><strong>Signed</strong><small>heartbeats</small></span>
          </div>
        </div>
      </section>
      <section className="auth-form-wrap">
        <form id={formId} className="auth-form" method="post" autoComplete="on" onSubmit={submit}>
          <div className="auth-heading">
            <span className="eyebrow">{bootstrapRequired ? 'First run' : 'Welcome back'}</span>
            <h2>{bootstrapRequired ? 'Create the administrator' : text('login.heading')}</h2>
            <p>{bootstrapRequired ? 'There is no default password. Use the one-time setup token configured on this server.' : text('login.subtitle')}</p>
          </div>
          {problem && <div id="authentication-error" className="form-error" role="alert"><strong>{problem.title}</strong><span>{problem.detail}</span></div>}
          {bootstrapRequired && <label htmlFor="bootstrap-display-name"><span>Administrator name</span><input id="bootstrap-display-name" name="display_name" autoComplete="name" required aria-describedby={errorId} /></label>}
          <label htmlFor={emailId}>
            <span>{bootstrapRequired ? 'Email address' : text('login.email_label')}</span>
            <input
              id={emailId}
              name={bootstrapRequired ? 'email' : 'username'}
              type="email"
              inputMode="email"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              required
              aria-describedby={errorId}
              aria-invalid={problem ? true : undefined}
            />
          </label>
          <div className="auth-field">
            <label htmlFor={passwordId}><span>{bootstrapRequired ? 'Password' : text('login.password_label')}</span></label>
            <span className="credential-input">
              <input
                ref={passwordInput}
                id={passwordId}
                name="password"
                type={passwordVisible ? 'text' : 'password'}
                autoComplete={bootstrapRequired ? 'new-password' : 'current-password'}
                minLength={14}
                required
                aria-describedby={errorId}
                aria-invalid={problem ? true : undefined}
              />
              <button
                type="button"
                className="password-visibility"
                aria-label={passwordVisible ? 'Hide password' : 'Show password'}
                aria-controls={passwordId}
                aria-pressed={passwordVisible}
                onPointerDownCapture={(event) => { rememberPasswordSelection(); event.preventDefault() }}
                onMouseDown={(event) => { event.preventDefault() }}
                onClick={togglePasswordVisibility}
              >
                {passwordVisible ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
              </button>
            </span>
          </div>
          {bootstrapRequired ? (
            <><div className="password-guidance"><Check size={15} /> At least 14 characters and three character classes</div><label htmlFor="bootstrap-secret"><span>One-time bootstrap secret</span><input id="bootstrap-secret" name="bootstrap_secret" type="password" autoComplete="one-time-code" required aria-describedby={errorId} /></label></>
          ) : (
            <label htmlFor="login-totp"><span>TOTP code <small>if enabled</small></span><input id="login-totp" name="totp_code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" aria-describedby={errorId} /></label>
          )}
          {!bootstrapRequired && text('login.help_text') && <p className="login-help">{text('login.help_text')}</p>}
          {!bootstrapRequired && text('login.support_url') && text('login.support_label') && <a className="login-support" href={text('login.support_url')} rel="noreferrer">{text('login.support_label')}</a>}
          <button type="submit" className="button primary auth-submit" disabled={mutation.isPending}>{mutation.isPending ? 'Securing session…' : bootstrapRequired ? 'Create administrator' : text('login.sign_in_button')}<ArrowRight size={18} /></button>
          <p className="auth-footnote">{bootstrapRequired ? 'Secure HttpOnly session · SameSite protection · Audited access' : text('login.footer')}</p>
        </form>
      </section>
    </main>
  )
}
