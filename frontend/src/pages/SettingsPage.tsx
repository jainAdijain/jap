import { useEffect, useState } from 'react'

import { api, type Run, type Switches } from '../api/client'
import { atsLabel, errorText, money, STARTED_BY, when } from '../format'
import { useApp } from '../appContext'

const hhmm = (t: string) => t.slice(0, 5)
const size = (bytes: number) => (bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`)

function problems(run: Run): [string, string] {
  if (run.status === 'failed') return [run.error ?? 'The search failed', 'error-text']
  if (run.status === 'running') return ['Searching now…', 'muted']
  if (run.status === 'queued') return ['Waiting to start', 'muted']
  const c = run.counts
  const out: string[] = []
  if (c.board_errors?.length) out.push(...c.board_errors)
  else if (c.boards_failed) out.push(`${c.boards_failed} companies could not be read`)
  if (c.spend_cap_reached) out.push('AI limit reached')
  if (c.daily_cap_reached) out.push('Daily sending limit reached')
  if (c.draft_failed) out.push(`${c.draft_failed} answers not written (AI model failed)`)
  return out.length ? [out.join('; '), 'warn-text'] : ['None', 'muted']
}

function SearchHistory() {
  const { tick } = useApp()
  const [runs, setRuns] = useState<Run[] | null>(null)
  useEffect(() => {
    api.runs().then(setRuns, () => setRuns([]))
  }, [tick])
  return (
    <div className="sec">
      <div className="sec-l">
        <h2>Search history</h2>
        <p>Every search for new jobs, what started it and what it found. JAP also searches after you save Job preferences or use the skills from a new resume.</p>
      </div>
      <div className="sec-r flush">
        {runs && runs.length === 0 ? (
          <p className="muted" style={{ padding: 20 }}>
            No searches yet.
          </p>
        ) : (
          <div className="table-scroll">
            <table className="hist">
              <colgroup>
                <col style={{ width: '16%' }} />
                <col style={{ width: '15%' }} />
                <col style={{ width: '18%' }} />
                <col style={{ width: '9%' }} />
                <col style={{ width: '8%' }} />
                <col style={{ width: '10%' }} />
                <col style={{ width: '24%' }} />
              </colgroup>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Started by</th>
                  <th>Job titles</th>
                  <th>Found</th>
                  <th>New</th>
                  <th>Applied</th>
                  <th>Problems</th>
                </tr>
              </thead>
              <tbody>
                {(runs ?? []).map((r) => {
                  const [text, cls] = problems(r)
                  const roles = r.scope.roles?.length ? r.scope.roles.join(', ') : 'All'
                  return (
                    <tr key={r.id}>
                      <td className="mono">{when(r.started_at ?? r.created_at)}</td>
                      <td>
                        {STARTED_BY[r.trigger]}
                        {r.mode === 'find_and_apply' && <span className="muted"> · and apply</span>}
                      </td>
                      <td title={roles}>{roles}</td>
                      <td className="mono">{r.counts.fetched ?? '–'}</td>
                      <td className="mono">{r.counts.new_listings ?? r.counts.new ?? '–'}</td>
                      <td className="mono">{r.mode === 'find_and_apply' ? (r.counts.applied ?? 0) : '–'}</td>
                      <td className={cls} title={text}>
                        {text}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

function SystemStatus() {
  const { health, healthError, ops, toast } = useApp()
  const [shot, setShot] = useState<string | null>(null)
  const start = async (what: 'backup' | 'forms') => {
    try {
      await (what === 'backup' ? api.backupNow() : api.formCheckNow())
      toast(what === 'backup' ? 'Backing up. The file shows here in a few seconds.' : 'Checking one real form on each system. Nothing is submitted.')
    } catch (err) {
      toast({ text: errorText(err), bad: true })
    }
  }
  const check = (k: string, name: string, okText: string, badText: string) => {
    const c = health?.checks[k]
    const ok = healthError ? false : c ? c.ok : null
    const detail = healthError ? (k === 'api' ? `Not answering: ${healthError}` : 'Unknown while the app server is down') : c ? (c.ok ? okText : `${badText}${c.detail ? ` (${c.detail})` : ''}`) : 'Checking…'
    return (
      <div key={k} className="hrow">
        <i className={ok === null ? '' : ok ? 'ok' : 'bad'} />
        <b>{name}</b>
        <span className="hd muted">{detail}</span>
      </div>
    )
  }
  const checks = ops ? new Map(ops.form_checks.map((c) => [c.ats, c])) : null
  const failed = ops?.form_checks.filter((c) => !c.ok) ?? []
  const lastCheck = ops?.form_checks.map((c) => c.checked_at).sort().pop()
  return (
    <div className="sec">
      <div className="sec-l">
        <h2>System status</h2>
        <p>Checked every few seconds.</p>
      </div>
      <div className="sec-r">
        <div className="health">
          {check('api', 'App server', 'Running', 'Not running')}
          {check('database', 'Database', 'Connected', 'Not connected')}
          {check('inngest', 'Job search', 'Ready', 'Not ready')}
          {check('ollama', 'Local AI', 'Running', 'Not running. Start Ollama.')}
          <div className="hrow">
          <i className={ops ? (ops.backup_today ? 'ok' : 'bad') : ''} />
          <b>Backup</b>
          <span className="hd muted">
            {!ops
              ? 'Checking…'
              : ops.last_backup
                ? `${ops.backup_today ? 'Today' : 'None today yet; last'}: ${ops.last_backup.file} (${size(ops.last_backup.bytes)}, ${when(ops.last_backup.at)}). ${ops.backups} kept for 14 days.`
                : 'No backup yet'}
          </span>
          <button type="button" onClick={() => void start('backup')}>
            Back up now
          </button>
          </div>
          <div className="hrow">
          <i className={!checks || checks.size === 0 ? '' : failed.length ? 'bad' : 'ok'} />
          <b>Form check</b>
          <span className="hd muted">
            {!checks || checks.size === 0
              ? 'Weekly, Mondays 07:00. Not run yet.'
              : (['greenhouse', 'lever', 'ashby'] as const)
                  .map((a) => {
                    const c = checks.get(a)
                    return c ? `${atsLabel(a)} ${c.ok ? `OK (${c.fields} fields)` : `failed: ${c.error ?? 'unknown'}`}` : `${atsLabel(a)} not checked`
                  })
                  .join(' · ') + (lastCheck ? `. Checked ${when(lastCheck)}.` : '')}
            {failed
              .filter((c) => c.has_screenshot)
              .map((c) => (
                <button key={c.id} type="button" className="btn-link" style={{ marginLeft: 8 }} onClick={() => setShot(shot === c.id ? null : c.id)}>
                  {shot === c.id ? 'Hide screenshot' : `${atsLabel(c.ats)} screenshot`}
                </button>
              ))}
          </span>
          <button type="button" onClick={() => void start('forms')}>
            Check now
          </button>
          </div>
        </div>
        {shot && <img className="shot" style={{ maxWidth: '100%' }} src={api.formCheckShotUrl(shot)} alt="The form page when the check failed" />}
      </div>
    </div>
  )
}

export default function SettingsPage() {
  const { profile, setProfile, toast } = useApp()
  const [time, setTime] = useState<string | null>(null)
  const [cap, setCap] = useState<string | null>(null)
  const [limit, setLimit] = useState<string | null>(null)
  const flip = async (s: Switches, done?: string) => {
    try {
      setProfile(await api.setSwitches(s))
      if (done) toast(done)
    } catch (err) {
      toast({ text: errorText(err), bad: true })
    }
  }
  if (!profile) {
    return (
      <section className="page">
        <p className="muted">Loading…</p>
      </section>
    )
  }
  const automatic = profile.apply_mode === 'automatic'
  const spend = profile.spend
  const pct = Math.min(100, spend.cap_usd ? (spend.month_usd / spend.cap_usd) * 100 : 100)
  const capValue = cap ?? String(profile.daily_cap)
  const limitValue = limit ?? spend.cap_usd.toFixed(2)
  return (
    <section className="page">
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p>How and when JAP works for you. Changes save straight away.</p>
        </div>
      </div>

      <div className="sec">
        <div className="sec-l">
          <h2>Applying</h2>
          <p>What JAP does with jobs you choose to apply to, and with new jobs a search finds when it is asked to apply.</p>
        </div>
        <div className="sec-r">
          <div className="radio-list" role="radiogroup" aria-label="Applying">
            <label>
              <input type="radio" name="mode" checked={!automatic} onChange={() => void flip({ apply_mode: 'review' }, 'JAP prepares each application; you send it.')} />
              <span>
                <b>JAP prepares, I send</b>
              </span>
              <span className="d">JAP fills in each form. Nothing is sent until you press Send application.</span>
            </label>
            <label>
              <input type="radio" name="mode" checked={automatic} onChange={() => void flip({ apply_mode: 'automatic' }, 'JAP now sends applications with nothing unclear.')} />
              <span>
                <b>JAP applies for me</b>
              </span>
              <span className="d">Forms with nothing unclear are sent, at least 30 seconds apart. Jobs with questions, or no work mode stated, wait for you.</span>
              {automatic && (
                <span className="more">
                  <span className="inline">
                    <label htmlFor="cap">Send at most</label>
                    <input
                      id="cap"
                      type="number"
                      min={0}
                      max={100}
                      style={{ width: 80 }}
                      value={capValue}
                      onChange={(e) => setCap(e.target.value)}
                      onBlur={() => {
                        const n = Number(cap)
                        if (cap !== null && Number.isInteger(n) && n >= 0 && n <= 100 && n !== profile.daily_cap) void flip({ daily_cap: n }, `At most ${n} a day.`)
                        setCap(null)
                      }}
                    />
                    <span>applications a day</span>
                  </span>
                </span>
              )}
            </label>
          </div>
          <div className="inline spread">
            <label className="inline" htmlFor="daily-at">
              Search for new jobs every day at
              <input
                id="daily-at"
                type="time"
                style={{ width: 'auto' }}
                value={time ?? hhmm(profile.daily_run_at)}
                onChange={(e) => setTime(e.target.value)}
                onBlur={() => {
                  if (time && time !== hhmm(profile.daily_run_at)) void flip({ daily_run_at: time }, `Daily search at ${time}.`)
                  setTime(null)
                }}
              />
            </label>
            <input
              type="checkbox"
              role="switch"
              className="sw"
              aria-label="Daily search"
              checked={profile.daily_run}
              onChange={(e) => void flip({ daily_run: e.target.checked }, e.target.checked ? `Daily search is on, at ${hhmm(profile.daily_run_at)}.` : 'Daily search is off.')}
            />
          </div>
          <p className="help">
            {profile.daily_run
              ? `Every day at ${hhmm(profile.daily_run_at)}, JAP searches every company you follow${automatic ? ' and sends what it can' : ''}. It runs only while this computer is awake.`
              : 'Off: JAP searches only when you press Find new jobs, save Job preferences or use a new resume.'}
          </p>
        </div>
      </div>

      <SearchHistory />

      <div className="sec">
        <div className="sec-l">
          <h2>AI usage</h2>
          <p>An AI model reads job descriptions and writes answers and cover letters.</p>
        </div>
        <div className="sec-r">
          <div className="inline spread">
            <span>
              <span className="mono">{money(spend.month_usd)}</span> of <span className="mono">{money(spend.cap_usd)}</span> used this month
            </span>
            <label className="inline">
              Monthly limit ($)
              <input
                type="number"
                min={0}
                max={100}
                step={0.5}
                style={{ width: 90 }}
                value={limitValue}
                onChange={(e) => setLimit(e.target.value)}
                onBlur={() => {
                  const n = Number(limit)
                  if (limit !== null && limit.trim() !== '' && Number.isFinite(n) && n >= 0 && n <= 100 && n !== spend.cap_usd) void flip({ spend_cap_usd: n }, `Monthly limit set to ${money(n)}.`)
                  setLimit(null)
                }}
              />
            </label>
          </div>
          <div className={spend.reached ? 'meter full' : 'meter'} role="meter" aria-valuemin={0} aria-valuemax={spend.cap_usd} aria-valuenow={spend.month_usd} aria-label="AI spend this month">
            <span style={{ width: `${pct}%` }} />
          </div>
          <span className={spend.reached ? 'error-text small' : 'muted small'}>
            {spend.reached
              ? 'Limit reached: JAP has stopped reading descriptions and writing answers and letters until next month or until you raise the limit.'
              : 'When the limit is reached, JAP stops reading descriptions and writing answers and letters until next month, and a banner shows on Jobs.'}
          </span>
        </div>
      </div>

      <SystemStatus />
    </section>
  )
}
