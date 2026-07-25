import { useMutation } from '@tanstack/react-query'
import { Eye, EyeOff, ShieldCheck, Zap } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from '../../app/router'
import { adaptSession } from '../../api/adapters'
import { errorMessage, request } from '../../api/client'
import { useAuth } from '../../state/AuthContext'

export function SignInPage() {
  const { session, refresh } = useAuth()
  const bootstrap = Boolean(session?.bootstrapRequired)
  const [showPassword, setShowPassword] = useState(false)
  const [totpVisible, setTotpVisible] = useState(false)
  const formRef = useRef<HTMLFormElement>(null)
  const navigate = useNavigate()
  const location = useLocation()
  const submit = useMutation({
    mutationFn: async (form: HTMLFormElement) => {
      const data = new FormData(form)
      const field = (name: string) => {
        const value = data.get(name)
        return typeof value === 'string' ? value : ''
      }
      const email = field('email')
      const password = field('password')
      const payload = bootstrap
        ? {
            bootstrap_secret: field('bootstrap_secret'),
            email,
            display_name: field('display_name'),
            password,
          }
        : {
            email,
            password,
            totp_code: field('totp_code') || undefined,
          }
      return request(
        bootstrap ? '/api/v1/auth/bootstrap' : '/api/v1/auth/login',
        { method: 'POST', body: JSON.stringify(payload) },
        adaptSession,
      )
    },
    onSuccess: async () => {
      if (bootstrap) localStorage.setItem('pm-onboarding-step', '2')
      await refresh()
      const from = (location.state as { from?: string } | null)?.from
      navigate(bootstrap ? '/onboarding' : from ?? '/home', { replace: true })
    },
    onError: (error) => {
      if (!bootstrap && errorMessage(error).toLowerCase().includes('verification code')) setTotpVisible(true)
    },
  })

  useEffect(() => {
    if (!session?.authenticated || !formRef.current) return
    formRef.current.reset()
  }, [session?.authenticated])

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    submit.mutate(event.currentTarget)
  }

  return (
    <main className="auth-page" id="main-content">
      <section className="auth-brand">
        <span className="brand-mark large"><Zap fill="currentColor" aria-hidden="true" /></span>
        <span>Power Monitor</span>
        <h1>Understand your home’s energy, privately.</h1>
        <p>Live power, billing estimates, and sensor health stay on the server you control.</p>
        <ul>
          <li><ShieldCheck /> Local-first and encrypted</li>
          <li><ShieldCheck /> Exact rate calculations</li>
          <li><ShieldCheck /> No cloud account required</li>
        </ul>
      </section>
      <section className="auth-card" aria-labelledby="auth-title">
        <div>
          <span className="icon-tile"><Zap aria-hidden="true" /></span>
          <p>{bootstrap ? 'First-time setup' : 'Welcome back'}</p>
          <h2 id="auth-title">{bootstrap ? 'Create the home owner' : 'Sign in to your home'}</h2>
          <span>{bootstrap ? 'Use the setup token from your private secrets dataset.' : 'Use your Power Monitor account.'}</span>
        </div>
        <form ref={formRef} method="post" action="/api/v1/auth/login" onSubmit={onSubmit} noValidate>
          {bootstrap && (
            <>
              <label htmlFor="bootstrap-secret">First-run setup token</label>
              <input id="bootstrap-secret" name="bootstrap_secret" type="password" autoComplete="off" required minLength={16} />
              <label htmlFor="display-name">Your name</label>
              <input id="display-name" name="display_name" type="text" autoComplete="name" required />
            </>
          )}
          <label htmlFor="email">Email</label>
          <input id="email" name="email" type="email" inputMode="email" autoComplete="username" required />
          <label htmlFor="current-password">{bootstrap ? 'Create password' : 'Password'}</label>
          <div className="password-field">
            <input
              id="current-password"
              name="password"
              type={showPassword ? 'text' : 'password'}
              autoComplete={bootstrap ? 'new-password' : 'current-password'}
              required
              minLength={bootstrap ? 14 : undefined}
            />
            <button type="button" aria-label={showPassword ? 'Hide password' : 'Show password'} onClick={() => { setShowPassword(!showPassword); }}>
              {showPassword ? <EyeOff /> : <Eye />}
            </button>
          </div>
          {totpVisible && (
            <>
              <label htmlFor="totp-code">Verification code</label>
              <input id="totp-code" name="totp_code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" />
            </>
          )}
          {submit.error && <p className="form-error" role="alert">{errorMessage(submit.error)}</p>}
          <button type="submit" className="button primary wide" disabled={submit.isPending}>
            {submit.isPending ? 'Signing in…' : bootstrap ? 'Create owner and continue' : 'Sign in'}
          </button>
        </form>
      </section>
    </main>
  )
}
