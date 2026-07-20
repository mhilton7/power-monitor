import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Activity, ArrowRight, Check, LockKeyhole, Server, ShieldCheck, Zap } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ApiError, api } from '../api'
import type { Session } from '../types'

export function AuthPage({ bootstrapRequired }: { bootstrapRequired: boolean }) {
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [bootstrapSecret, setBootstrapSecret] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()
  const destination = (location.state as { from?: string } | null)?.from ?? '/'
  const mutation = useMutation({
    mutationFn: () =>
      api<Session>(bootstrapRequired ? '/api/v1/auth/bootstrap' : '/api/v1/auth/login', {
        method: 'POST',
        body: JSON.stringify(
          bootstrapRequired
            ? { email, display_name: displayName, password, bootstrap_secret: bootstrapSecret }
            : { email, password, totp_code: totpCode || undefined },
        ),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['session'] })
      await navigate(destination, { replace: true })
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    mutation.mutate()
  }
  const problem = mutation.error instanceof ApiError ? mutation.error.problem : undefined

  return (
    <main className="auth-page">
      <section className="auth-story">
        <div className="auth-brand">
          <span className="brand-mark"><Zap fill="currentColor" /></span>
          <strong>Power Monitor Server</strong>
        </div>
        <div className="auth-story-copy">
          <span className="eyebrow">Private fleet intelligence</span>
          <h1>Every circuit.<br />One trustworthy view.</h1>
          <p>
            Consolidate live ESP32-S3 measurements, recover microSD history, and estimate time-of-use costs without
            sending household data to a cloud service.
          </p>
          <div className="auth-proof">
            <span><Activity /><strong>5 sec</strong><small>live target</small></span>
            <span><Server /><strong>Local</strong><small>data custody</small></span>
            <span><ShieldCheck /><strong>Signed</strong><small>device traffic</small></span>
          </div>
        </div>
        <p className="auth-protocol"><LockKeyhole size={15} /> pm-protocol/1.0.0 · HMAC authenticated</p>
      </section>
      <section className="auth-form-wrap">
        <form className="auth-form" onSubmit={submit}>
          <div className="auth-heading">
            <span className="eyebrow">{bootstrapRequired ? 'First run' : 'Welcome back'}</span>
            <h2>{bootstrapRequired ? 'Create the administrator' : 'Sign in to your fleet'}</h2>
            <p>
              {bootstrapRequired
                ? 'There is no default password. Use the one-time secret configured on this server.'
                : 'Your secure session is limited to this Power Monitor server.'}
            </p>
          </div>
          {problem && (
            <div className="form-error" role="alert"><strong>{problem.title}</strong><span>{problem.detail}</span></div>
          )}
          {bootstrapRequired && (
            <label>
              <span>Administrator name</span>
              <input autoComplete="name" value={displayName} onChange={(event) => { setDisplayName(event.target.value); }} required />
            </label>
          )}
          <label>
            <span>Email address</span>
            <input type="email" autoComplete="email" value={email} onChange={(event) => { setEmail(event.target.value); }} required />
          </label>
          <label>
            <span>Password</span>
            <input type="password" autoComplete={bootstrapRequired ? 'new-password' : 'current-password'} value={password} onChange={(event) => { setPassword(event.target.value); }} minLength={14} required />
          </label>
          {bootstrapRequired ? (
            <>
              <div className="password-guidance">
                <Check size={15} /> At least 14 characters and three character classes
              </div>
              <label>
                <span>One-time bootstrap secret</span>
                <input type="password" autoComplete="off" value={bootstrapSecret} onChange={(event) => { setBootstrapSecret(event.target.value); }} required />
              </label>
            </>
          ) : (
            <label>
              <span>TOTP code <small>if enabled</small></span>
              <input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" value={totpCode} onChange={(event) => { setTotpCode(event.target.value); }} />
            </label>
          )}
          <button className="button primary auth-submit" disabled={mutation.isPending}>
            {mutation.isPending ? 'Securing session…' : bootstrapRequired ? 'Create administrator' : 'Sign in'}
            <ArrowRight size={18} />
          </button>
          <p className="auth-footnote">Secure HttpOnly session · SameSite protection · Audited access</p>
        </form>
      </section>
    </main>
  )
}
