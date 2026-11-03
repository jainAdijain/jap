import { useState, type FormEvent } from 'react'

import { auth, type TwoStepSetup } from '../api/client'
import { useApp } from '../appContext'
import { useAuth } from '../authContext'
import { errorText } from '../format'

// Settings > Account (PRD U21): password, two-step sign-in with recovery codes, sign out.

type Open = null | 'password' | 'two-step-off' | 'new-codes'

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const text = codes.join('\n')
  return (
    <div className="account-form">
      <p className="warn-text small">
        Save these recovery codes somewhere safe, such as your password manager. Each one signs you in once if you lose your phone. They won't be shown again.
      </p>
      <div className="codes mono">
        {codes.map((c) => (
          <span key={c}>{c}</span>
        ))}
      </div>
      <div className="inline">
        <button type="button" onClick={() => void navigator.clipboard?.writeText(text)}>
          Copy
        </button>
        <a className="btn" href={`data:text/plain;charset=utf-8,${encodeURIComponent(`JAP recovery codes\n\n${text}\n`)}`} download="jap-recovery-codes.txt">
          Download
        </a>
        <button type="button" className="btn-primary" onClick={onDone}>
          I've saved them
        </button>
      </div>
    </div>
  )
}

export default function AccountSection() {
  const { user, setUser, signOut } = useAuth()
  const { toast } = useApp()
  const [open, setOpen] = useState<Open>(null)
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const [setup, setSetup] = useState<TwoStepSetup | null>(null)
  const [code, setCode] = useState('')
  const [codes, setCodes] = useState<string[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const reset = () => {
    setOpen(null)
    setCurrent('')
    setNext('')
    setAgain('')
    setCode('')
    setError(null)
  }
  const run = (fn: () => Promise<void>) => async (e?: FormEvent) => {
    e?.preventDefault()
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
  const refreshUser = async () => setUser(await auth.me())
  const failure = error && (
    <p className="error-text small" role="alert">
      {error}
    </p>
  )
  const passwordField = (label: string, value: string, set: (v: string) => void, autoComplete: string) => (
    <label className="auth-field">
      <span>{label}</span>
      <input type="password" value={value} onChange={(e) => set(e.target.value)} autoComplete={autoComplete} required />
    </label>
  )
  const cancel = (
    <button type="button" className="btn-quiet" onClick={reset}>
      Cancel
    </button>
  )

  return (
    <div className="sec">
      <div className="sec-l">
        <h2>Account</h2>
        <p>How you sign in to JAP. Sessions end after 7 days without use, or 30 days after you sign in.</p>
      </div>
      <div className="sec-r">
        <div className="inline spread">
          <span>
            Signed in as <b>{user.email}</b>
            {user.is_admin && <span className="muted"> · admin</span>}
          </span>
          {open !== 'password' && (
            <button type="button" onClick={() => setOpen('password')}>
              Change password
            </button>
          )}
        </div>
        {open === 'password' && (
          <form
            className="account-form"
            onSubmit={run(async () => {
              if (next.length < 12) throw new Error('Use at least 12 characters')
              if (next !== again) throw new Error('The two new passwords are different')
              await auth.changePassword(current, next)
              reset()
              toast('Password changed. Other browsers are signed out.')
            })}
          >
            {passwordField('Current password', current, setCurrent, 'current-password')}
            {passwordField('New password', next, setNext, 'new-password')}
            {passwordField('New password again', again, setAgain, 'new-password')}
            {failure}
            <div className="inline">
              <button type="submit" className="btn-primary" disabled={busy}>
                Change password
              </button>
              {cancel}
            </div>
          </form>
        )}

        <hr className="rule" />

        <div className="inline spread">
          <span>
            <b>Two-step sign-in</b>{' '}
            {user.two_step ? (
              <span className="muted">
                · on, {user.recovery_codes_left} recovery {user.recovery_codes_left === 1 ? 'code' : 'codes'} left
              </span>
            ) : (
              <span className="muted">· off</span>
            )}
          </span>
          {!user.two_step && !setup && !codes && (
            <button type="button" disabled={busy} onClick={() => void run(async () => setSetup(await auth.twoStepStart()))()}>
              Turn on
            </button>
          )}
          {user.two_step && !open && !codes && (
            <span className="inline">
              <button type="button" onClick={() => setOpen('new-codes')}>
                New recovery codes
              </button>
              <button type="button" className="btn-danger" onClick={() => setOpen('two-step-off')}>
                Turn off
              </button>
            </span>
          )}
        </div>
        <p className="help">After your password, JAP also asks for a 6-digit code from an authenticator app on your phone, such as Google Authenticator or 1Password.</p>

        {setup && (
          <form
            className="account-form"
            onSubmit={run(async () => {
              const res = await auth.twoStepConfirm(code)
              setSetup(null)
              setCode('')
              setCodes(res.recovery_codes)
              await refreshUser()
            })}
          >
            <p className="small">1. Scan this with your authenticator app.</p>
            <div className="qr" dangerouslySetInnerHTML={{ __html: setup.qr_svg }} aria-label="QR code for your authenticator app" role="img" />
            <p className="small muted">
              Can't scan it? Enter this key: <span className="mono">{setup.secret.replace(/(.{4})/g, '$1 ').trim()}</span>
            </p>
            <label className="auth-field">
              <span>2. Enter the 6-digit code it shows</span>
              <input type="text" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} required />
            </label>
            {failure}
            <div className="inline">
              <button type="submit" className="btn-primary" disabled={busy}>
                Turn on
              </button>
              <button type="button" className="btn-quiet" onClick={() => setSetup(null)}>
                Cancel
              </button>
            </div>
          </form>
        )}
        {codes && <RecoveryCodes codes={codes} onDone={() => setCodes(null)} />}
        {(open === 'two-step-off' || open === 'new-codes') && (
          <form
            className="account-form"
            onSubmit={run(async () => {
              if (open === 'two-step-off') {
                await auth.twoStepOff(current)
                toast('Two-step sign-in is off.')
              } else {
                setCodes((await auth.newRecoveryCodes(current)).recovery_codes)
              }
              reset()
              await refreshUser()
            })}
          >
            {passwordField('Your password', current, setCurrent, 'current-password')}
            {failure}
            <div className="inline">
              <button type="submit" className={open === 'two-step-off' ? 'btn-danger' : 'btn-primary'} disabled={busy}>
                {open === 'two-step-off' ? 'Turn off two-step sign-in' : 'Make new codes'}
              </button>
              {cancel}
            </div>
          </form>
        )}

        <hr className="rule" />

        <div className="inline">
          <button type="button" onClick={() => void signOut()}>
            Sign out
          </button>
          <button type="button" className="btn-quiet" onClick={() => void signOut(true)}>
            Sign out everywhere
          </button>
        </div>
      </div>
    </div>
  )
}
