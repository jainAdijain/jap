import { useEffect, useState } from 'react'

import type { RunRequest } from '../api/client'

type Props = {
  roles: string[]
  busy: boolean
  automatic?: boolean
  onClose: () => void
  onStart: (body: RunRequest) => Promise<void>
}

/** The Run dialog (T3, U5): which roles to refresh, and Find only or Find and apply. */
export default function RunDialog({ roles, busy, automatic = false, onClose, onStart }: Props) {
  const [all, setAll] = useState(true)
  const [picked, setPicked] = useState<string[]>([])
  const [mode, setMode] = useState<RunRequest['mode']>('find_only')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const toggle = (role: string) =>
    setPicked((p) => (p.includes(role) ? p.filter((r) => r !== role) : [...p, role]))
  const ready = all || picked.length > 0

  const start = async () => {
    setSaving(true)
    setError(null)
    try {
      await onStart(all ? { mode } : { mode, roles: picked })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setSaving(false)
    }
  }

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="run-dialog-title" onClick={(e) => e.stopPropagation()}>
        <h2 id="run-dialog-title">Run</h2>
        <fieldset>
          <legend>Which jobs</legend>
          <label className="choice">
            <input type="radio" name="scope" checked={all} onChange={() => setAll(true)} />
            Every job on the companies I watch
          </label>
          <label className="choice">
            <input type="radio" name="scope" checked={!all} disabled={roles.length === 0} onChange={() => setAll(false)} />
            Only some of my roles
            {roles.length === 0 && <span className="muted"> (add target roles in My profile first)</span>}
          </label>
          {!all && (
            <div className="role-picks">
              {roles.map((r) => (
                <label key={r} className="choice">
                  <input type="checkbox" checked={picked.includes(r)} onChange={() => toggle(r)} />
                  {r}
                </label>
              ))}
            </div>
          )}
        </fieldset>
        <fieldset>
          <legend>What to do</legend>
          <label className="choice">
            <input type="radio" name="mode" checked={mode === 'find_only'} onChange={() => setMode('find_only')} />
            Find only: list the jobs, don&apos;t apply
          </label>
          <label className="choice">
            <input type="radio" name="mode" checked={mode === 'find_and_apply'} onChange={() => setMode('find_and_apply')} />
            Find and apply: prepare every Not applied job
          </label>
          {mode === 'find_and_apply' && (
            <p className="hint">
              {automatic
                ? 'Automatic mode: each job with nothing unclear is submitted, at least 30 seconds apart, up to your daily cap. The rest wait for you.'
                : 'Review mode: each job stops at Ready for review, and nothing is sent until you approve it. Up to your daily cap.'}
            </p>
          )}
        </fieldset>
        {busy && <p className="hint">A run is going. This one waits for it, and joins any run already queued.</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <div className="actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="primary" disabled={!ready || saving} onClick={() => void start()}>
            {saving ? 'Starting…' : 'Start run'}
          </button>
        </div>
      </div>
    </div>
  )
}
