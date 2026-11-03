import { useState, type FormEvent, type ReactNode } from 'react'

import { auth, type AuthState, type User } from '../api/client'
import { errorText } from '../format'

// The screen shown before JAP opens (PRD S1 to S6): create the first account, sign in,
// the two-step code, Forgot password and the reset link from the email.

type Mode = 'sign-in' | 'create' | 'code' | 'forgot' | 'forgot-sent' | 'reset' | 'reset-done'

const MIN_PASSWORD = 12

function Card({ title, lead, children }: { title: string; lead?: string; children: ReactNode }) {
  return (
    <main className="auth">
      <div className="auth-card">
        <div className="logo auth-logo">
          <span className="logo-mark">JAP</span>
          <span className="logo-name">Job Application Pipeline</span>
        </div>
        <h1>{title}</h1>
        {lead && <p className="muted">{lead}</p>}
        {children}
      </div>
    </main>
  )
}

function Field(props: {
  label: string
  type: string
  value: string
  onChange: (v: string) => void
  autoComplete: string
  hint?: string
  inputMode?: 'numeric' | 'text'
  autoFocus?: boolean
}) {
  return (
    <label className="auth-field">
      <span>{props.label}</span>
      <input
        type={props.type}
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        autoComplete={props.autoComplete}
        inputMode={props.inputMode}
        autoFocus={props.autoFocus}
        required
      />
      {props.hint && <small className="muted">{props.hint}</small>}
    </label>
  )
}

/** The reset token from the emailed link (?reset=...), taken out of the address bar at once. */
function takeResetToken(): string | null {
  const params = new URLSearchParams(window.location.search)
  const token = params.get('reset')
  if (token) {
    params.delete('reset')
    const rest = params.toString()
    window.history.replaceState(null, '', `${window.location.pathname}${rest ? `?${rest}` : ''}${window.location.hash}`)
  }
  return token
}

export default function SignInPage({ state, onSignedIn }: { state: AuthState; onSignedIn: (u: User) => void }) {
  const [resetToken] = useState(takeResetToken)
  const [mode, setMode] = useState<Mode>(() =>
    resetToken ? 'reset' : state.two_step_pending ? 'code' : state.sign_up_open ? 'create' : 'sign-in',
  )
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [again, setAgain] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const go = (m: Mode) => {
    setError(null)
    setPassword('')
    setAgain('')
    setCode('')
    setMode(m)
  }
  const run = (fn: () => Promise<void>) => async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await fn()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }
  const newPasswordProblem = () => {
    if (password.length < MIN_PASSWORD) return `Use at least ${MIN_PASSWORD} characters`
    if (password !== again) return 'The two passwords are different'
    return null
  }
  const failure = error && (
    <p className="error-text small" role="alert">
      {error}
    </p>
  )
  const submit = (label: string) => (
    <button type="submit" className="btn-primary auth-submit" disabled={busy}>
      {busy ? 'One moment…' : label}
    </button>
  )

  if (mode === 'create') {
    return (
      <Card title="Create your JAP account" lead="This first account is the admin of this JAP.">
        <form
          className="auth-form"
          onSubmit={run(async () => {
            const problem = newPasswordProblem()
            if (problem) throw new Error(problem)
            const res = await auth.signUp(email, password)
            if (res.user) onSignedIn(res.user)
          })}
        >
          <Field label="Email" type="email" value={email} onChange={setEmail} autoComplete="email" autoFocus />
          <Field label="Password" type="password" value={password} onChange={setPassword} autoComplete="new-password" hint={`At least ${MIN_PASSWORD} characters.`} />
          <Field label="Password again" type="password" value={again} onChange={setAgain} autoComplete="new-password" />
          {failure}
          {submit('Create account')}
        </form>
      </Card>
    )
  }

  if (mode === 'code') {
    return (
      <Card title="Two-step sign-in" lead="Enter the 6-digit code from your authenticator app, or one of your recovery codes.">
        <form
          className="auth-form"
          onSubmit={run(async () => {
            const res = await auth.code(code)
            if (res.user) onSignedIn(res.user)
          })}
        >
          <Field label="Code" type="text" value={code} onChange={setCode} autoComplete="one-time-code" inputMode="text" autoFocus />
          {failure}
          {submit('Sign in')}
          <button type="button" className="btn-link" onClick={() => go('sign-in')}>
            Start again with your password
          </button>
        </form>
      </Card>
    )
  }

  if (mode === 'forgot') {
    return (
      <Card title="Forgot your password?" lead="We'll email you a link to set a new one. It works once, for 30 minutes.">
        <form
          className="auth-form"
          onSubmit={run(async () => {
            await auth.forgot(email)
            go('forgot-sent')
          })}
        >
          <Field label="Email" type="email" value={email} onChange={setEmail} autoComplete="email" autoFocus />
          {failure}
          {submit('Email me a link')}
          <button type="button" className="btn-link" onClick={() => go('sign-in')}>
            Back to sign in
          </button>
        </form>
      </Card>
    )
  }

  if (mode === 'forgot-sent') {
    return (
      <Card title="Check your email" lead={`If ${email} has a JAP account, a link to set a new password is on its way. It works for 30 minutes.`}>
        <button type="button" className="btn-link" onClick={() => go('sign-in')}>
          Back to sign in
        </button>
      </Card>
    )
  }

  if (mode === 'reset') {
    return (
      <Card title="Set a new password" lead="Every browser signed in to your account will be signed out.">
        <form
          className="auth-form"
          onSubmit={run(async () => {
            const problem = newPasswordProblem()
            if (problem) throw new Error(problem)
            await auth.reset(resetToken ?? '', password)
            go('reset-done')
          })}
        >
          <Field label="New password" type="password" value={password} onChange={setPassword} autoComplete="new-password" hint={`At least ${MIN_PASSWORD} characters.`} autoFocus />
          <Field label="New password again" type="password" value={again} onChange={setAgain} autoComplete="new-password" />
          {failure}
          {submit('Set password')}
          <button type="button" className="btn-link" onClick={() => go('forgot')}>
            Ask for a new link
          </button>
        </form>
      </Card>
    )
  }

  if (mode === 'reset-done') {
    return (
      <Card title="Password changed" lead="Sign in with your new password.">
        <button type="button" className="btn-primary auth-submit" onClick={() => go('sign-in')}>
          Sign in
        </button>
      </Card>
    )
  }

  return (
    <Card title="Sign in to JAP">
      <form
        className="auth-form"
        onSubmit={run(async () => {
          const res = await auth.signIn(email, password)
          if (res.two_step) go('code')
          else if (res.user) onSignedIn(res.user)
        })}
      >
        <Field label="Email" type="email" value={email} onChange={setEmail} autoComplete="username" autoFocus />
        <Field label="Password" type="password" value={password} onChange={setPassword} autoComplete="current-password" />
        {failure}
        {submit('Sign in')}
        <button type="button" className="btn-link" onClick={() => go('forgot')}>
          Forgot password?
        </button>
      </form>
    </Card>
  )
}
