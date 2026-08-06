import { useMutation } from '@tanstack/react-query'
import { ShieldCheck, X } from 'lucide-react'
import { useState } from 'react'

import { errorMessage, json, request } from '../../api/client'
import { InlineNotice } from '../feedback/States'

export function ProtectedChangeDialog({
  mfaEnabled,
  onCancel,
  onConfirmed,
  eyebrow = 'Security confirmation',
  title = 'Confirm this protected change',
  description,
  submitLabel = 'Confirm and save',
}: {
  mfaEnabled: boolean
  onCancel: () => void
  onConfirmed: () => void
  eyebrow?: string
  title?: string
  description?: string
  submitLabel?: string
}) {
  const [password, setPassword] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const confirmationReady = password.length > 0 || (mfaEnabled && /^\d{6}$/.test(totpCode))
  const confirmChange = useMutation({
    mutationFn: () => request('/api/v1/auth/reauthenticate', json('POST', {
      password: password || undefined,
      totp_code: mfaEnabled && totpCode ? totpCode : undefined,
    })),
    onSuccess: onConfirmed,
  })

  return (
    <div className="modal-backdrop protected-change-backdrop">
      <form
        className="modal-card small-modal protected-change-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="protected-change-title"
        aria-describedby="protected-change-description"
        onSubmit={(event) => { event.preventDefault(); if (confirmationReady) confirmChange.mutate() }}
      >
        <header>
          <div><small>{eyebrow}</small><h2 id="protected-change-title">{title}</h2></div>
          <button className="icon-button" type="button" aria-label="Cancel protected change" onClick={onCancel}><X /></button>
        </header>
        <div className="setup-body form-grid single">
          <div className="protected-change-intro" id="protected-change-description">
            <ShieldCheck aria-hidden="true" />
            <p>{description ?? (mfaEnabled ? 'Enter either your current password or a six-digit MFA code.' : 'Enter your current password to save these high-risk permission changes.')}</p>
          </div>
          <label>Current password<input autoFocus={!mfaEnabled} type="password" autoComplete="current-password" value={password} onChange={(event) => { setPassword(event.target.value) }} /></label>
          {mfaEnabled && <label>MFA code<input autoFocus inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} placeholder="6-digit code" value={totpCode} onChange={(event) => { setTotpCode(event.target.value.replace(/\D/g, '').slice(0, 6)) }} /></label>}
          {confirmChange.error && <InlineNotice tone="danger">{errorMessage(confirmChange.error)}</InlineNotice>}
        </div>
        <footer>
          <button className="button secondary" type="button" onClick={onCancel}>Cancel</button>
          <button className="button primary" type="submit" disabled={!confirmationReady || confirmChange.isPending}>{confirmChange.isPending ? 'Confirming…' : submitLabel}</button>
        </footer>
      </form>
    </div>
  )
}
