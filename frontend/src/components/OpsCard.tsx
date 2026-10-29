import { useCallback, useEffect, useState } from 'react'

import { api, type Ops } from '../api/client'

const ATS_NAMES = { greenhouse: 'Greenhouse', lever: 'Lever', ashby: 'Ashby' } as const
const REFRESH_MS = 10000

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

const size = (bytes: number) => (bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`)

/** Hardening on the Status card (Phase 8): the daily backup and the weekly form test. */
export default function OpsCard() {
  const [ops, setOps] = useState<Ops | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [shot, setShot] = useState<string | null>(null)

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      setOps(await api.ops(signal))
    } catch {
      if (!signal?.aborted) setOps(null)
    }
  }, [])

  useEffect(() => {
    const ctrl = new AbortController()
    void refresh(ctrl.signal)
    const timer = setInterval(() => void refresh(ctrl.signal), REFRESH_MS)
    return () => {
      ctrl.abort()
      clearInterval(timer)
    }
  }, [refresh])

  const start = async (what: 'backup' | 'forms') => {
    try {
      await (what === 'backup' ? api.backupNow() : api.formCheckNow())
      setNote(what === 'backup' ? 'Backup started; the file appears here in a few seconds.' : 'Form test started: one real form per ATS is read, nothing is submitted.')
    } catch (err) {
      setNote(String(err))
    }
  }

  if (!ops) return null
  const checks = new Map(ops.form_checks.map((c) => [c.ats, c]))
  return (
    <div className="ops" data-testid="ops">
      <h3>Backups and form tests</h3>
      <ul className="ops-list">
        <li>
          <strong>Daily backup</strong>
          {ops.last_backup ? (
            <span className={ops.backup_today ? 'ok' : 'bad'}>
              {ops.backup_today ? 'Today: ' : 'None today yet. Last: '}
              data/backups/{ops.last_backup.file} ({size(ops.last_backup.bytes)}, {when(ops.last_backup.at)})
            </span>
          ) : (
            <span className="bad">No backup yet</span>
          )}
          <span className="muted">
            {ops.backups} kept (14 days). Made once a day, on the first hour the Mac is awake.
          </span>
        </li>
        {(['greenhouse', 'lever', 'ashby'] as const).map((ats) => {
          const c = checks.get(ats)
          return (
            <li key={ats}>
              <strong>{ATS_NAMES[ats]} form</strong>
              {c ? (
                <>
                  <span className={c.ok ? 'ok' : 'bad'}>
                    {c.ok ? `Read OK: ${c.fields} fields, ${c.required} required` : `Failed: ${c.error ?? 'unknown'}`}
                  </span>
                  <span className="muted">tested {when(c.checked_at)}{c.ok && c.error ? ` (${c.error})` : ''}</span>
                  {c.has_screenshot && !c.ok && (
                    <button type="button" className="link" onClick={() => setShot(shot === c.id ? null : c.id)}>
                      {shot === c.id ? 'Hide screenshot' : 'Screenshot'}
                    </button>
                  )}
                </>
              ) : (
                <span className="muted">Not tested yet (weekly, Mondays 07:00)</span>
              )}
            </li>
          )
        })}
        {shot && <li><img src={api.formCheckShotUrl(shot)} alt="The form page when the test failed" /></li>}
      </ul>
      <div className="row-buttons">
        <button type="button" onClick={() => void start('backup')}>
          Back up now
        </button>
        <button type="button" onClick={() => void start('forms')}>
          Test forms now
        </button>
      </div>
      {note && (
        <p className="hint" role="status">
          {note}
        </p>
      )}
    </div>
  )
}
