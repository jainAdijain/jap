import { useEffect, useRef, useState } from 'react'

import { api, type Application, type Ats, type FormField, type ListingDetail } from '../api/client'
import { atsLabel, shortDate } from '../format'

type Props = { listing: ListingDetail; onChanged: (detail?: ListingDetail) => void }

const SOURCES: Record<string, string> = {
  resume: 'Your resume',
  answer_bank: 'Answer bank',
  drafted: 'Drafted for this job',
  you: 'Your answer',
}

function sourceLabel(from: string): string {
  if (from.startsWith('profile:')) return 'My profile'
  return SOURCES[from] ?? ''
}

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })

/** One form field while Ready for review: every answer is shown and editable. */
function FieldInput({ f, value, onChange }: { f: FormField; value: string; onChange: (v: string) => void }) {
  const id = `field-${f.key}`
  let input
  if (f.type === 'file') {
    input = <p className="muted">{value || 'Nothing attached'}</p>
  } else if (f.type === 'textarea' || value.length > 120) {
    input = <textarea id={id} rows={Math.min(12, Math.max(4, Math.ceil(value.length / 55)))} maxLength={f.max_chars ?? undefined} value={value} onChange={(e) => onChange(e.target.value)} />
  } else if ((f.type === 'select' || f.type === 'radio') && f.options.length > 0) {
    input = (
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Leave empty</option>
        {f.options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    )
  } else if (f.type === 'checkbox') {
    input = (
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Leave unticked</option>
        <option value="Yes">Tick it</option>
      </select>
    )
  } else {
    input = <input id={id} type="text" value={value} onChange={(e) => onChange(e.target.value)} />
  }
  const from = sourceLabel(f.filled_from)
  return (
    <li className={`form-answer${f.filled_from === 'drafted' ? ' drafted' : ''}`}>
      <div className="form-answer-head">
        <label htmlFor={id}>
          {f.label}
          {f.required && <span className="req" aria-label="required"> *</span>}
        </label>
        {from && <span className={`from from-${f.filled_from.split(':')[0]}`}>{from}</span>}
      </div>
      {input}
      {f.max_chars && f.type !== 'file' && (
        <div className="hint">
          {value.length} of {f.max_chars} characters
        </div>
      )}
    </li>
  )
}

/** Apply now, Skip, Ready for review, Needs input, Applied and Failed (Statuses, U14, U15). */
export default function ApplyPanel({ listing, onChanged }: Props) {
  const app: Application | null = listing.application
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [saveToBank, setSaveToBank] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [queued, setQueued] = useState(false)

  const appId = app?.id
  const appStatus = app?.status
  useEffect(() => {
    setEdits({})
    setAnswers({})
    setError(null)
  }, [appId, appStatus])
  useEffect(() => setQueued(false), [listing.status, appId])
  // After Apply now or Retry, check every 2 s until the attempt shows up.
  const changed = useRef(onChanged)
  useEffect(() => {
    changed.current = onChanged
  }, [onChanged])
  useEffect(() => {
    if (!queued) return
    const timer = setInterval(() => changed.current(), 2000)
    return () => clearInterval(timer)
  }, [queued])

  const act = async (fn: () => Promise<ListingDetail | void>) => {
    setBusy(true)
    setError(null)
    try {
      onChanged((await fn()) ?? undefined)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const status = listing.status
  const live = app && ['preparing', 'ready_for_review', 'applying', 'needs_input'].includes(app.status) ? app : null
  const dirty = Object.keys(edits).length > 0
  const valueOf = (f: FormField) => edits[f.key] ?? f.value
  const skipButton = (
    <button type="button" disabled={busy} onClick={() => void act(() => api.skip(listing.id))}>
      Skip
    </button>
  )

  if (status === 'skipped') {
    return (
      <section className="apply">
        <h3>Application</h3>
        <p className="muted">You skipped this job.</p>
        <button type="button" disabled={busy} onClick={() => void act(() => api.unskip(listing.id))}>
          Undo
        </button>
        {error && <p className="error" role="alert">{error}</p>}
      </section>
    )
  }

  if (status === 'applied' && app?.status === 'applied') {
    return (
      <section className="apply">
        <h3>What was sent</h3>
        <dl className="facts">
          <div>
            <dt>Applied</dt>
            <dd>{app.submitted_at ? when(app.submitted_at) : '–'}</dd>
          </div>
          <div>
            <dt>Through</dt>
            <dd>{app.jd ? atsLabel(app.jd.source as Ats) : '–'}</dd>
          </div>
          <div>
            <dt>Resume version</dt>
            <dd>
              {app.resume ? (
                <a href={api.resumeFileUrl(app.resume.id)} target="_blank" rel="noreferrer">
                  {app.resume.original_name}
                </a>
              ) : (
                '–'
              )}
              {app.resume && <span className="muted"> · uploaded {shortDate(app.resume.uploaded_at)}</span>}
            </dd>
          </div>
          <div>
            <dt>Confirmation email</dt>
            <dd>{app.email_sent_at ? `Sent ${when(app.email_sent_at)}` : 'Not sent yet'}</dd>
          </div>
        </dl>
        <h4>Form answers</h4>
        <dl className="sent">
          {app.fields
            .filter((f) => f.value)
            .map((f) => (
              <div key={f.key}>
                <dt>{f.label}</dt>
                <dd>{f.value}</dd>
              </div>
            ))}
        </dl>
        {app.cover_letter_text && (
          <>
            <h4>Cover letter</h4>
            <div className="jd">{app.cover_letter_text}</div>
          </>
        )}
        {app.has_screenshot && (
          <>
            <h4>Confirmation screenshot</h4>
            <a href={api.fileUrl('screenshot', app.id)} target="_blank" rel="noreferrer">
              <img className="shot" src={api.fileUrl('screenshot', app.id)} alt="The page the employer showed after submitting" />
            </a>
          </>
        )}
      </section>
    )
  }

  return (
    <section className="apply">
      <h3>Application</h3>
      {error && <p className="error" role="alert">{error}</p>}

      {!live && (status === 'not_applied' || status === 'failed') && (
        <>
          {status === 'failed' && app && (
            <div className="failed">
              <p className="error">Failed: {app.error ?? 'unknown error'}</p>
              {app.has_screenshot && (
                <a href={api.fileUrl('screenshot', app.id)} target="_blank" rel="noreferrer">
                  <img className="shot" src={api.fileUrl('screenshot', app.id)} alt="The page when the attempt failed" />
                </a>
              )}
            </div>
          )}
          {queued ? (
            <p className="hint" role="status">Starting: JAP opens the form and fills it from My profile…</p>
          ) : listing.apply_blocked ? (
            <p className="muted">{listing.apply_blocked}.</p>
          ) : (
            <p className="hint">
              Review mode: JAP fills the form and stops at Ready for review. Nothing is sent until you press Approve.
            </p>
          )}
          <div className="row-buttons">
            {status === 'failed' && app ? (
              <button type="button" className="primary" disabled={busy || queued} onClick={() => void act(async () => { await api.retry(app.id); setQueued(true) })}>
                Retry
              </button>
            ) : (
              <button
                type="button"
                className="primary"
                disabled={busy || queued || !!listing.apply_blocked}
                onClick={() => void act(async () => { await api.applyNow(listing.id); setQueued(true) })}
              >
                Apply now
              </button>
            )}
            {skipButton}
          </div>
        </>
      )}

      {live?.status === 'preparing' && (
        <p className="hint" role="status">Preparing: reading the form and filling it from My profile, your resume and the answer bank…</p>
      )}
      {live?.status === 'applying' && (
        <p className="hint" role="status">Submitting the form… a screenshot of the confirmation follows.</p>
      )}

      {live?.status === 'needs_input' && (
        <div className="needs-input">
          <p className="lead">JAP stopped at {live.pending.length === 1 ? 'a question' : 'questions'} only you can answer.</p>
          <ul className="form-answers">
            {live.pending.map((q) => (
              <li key={q.key} className="form-answer">
                <label htmlFor={`q-${q.key}`}>{q.label}</label>
                <div className="hint">{q.reason}</div>
                {q.blocked ? null : q.options.length > 0 ? (
                  <select id={`q-${q.key}`} value={answers[q.key] ?? ''} onChange={(e) => setAnswers({ ...answers, [q.key]: e.target.value })}>
                    <option value="">Choose…</option>
                    {q.options.map((o) => (
                      <option key={o} value={o}>
                        {o}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input id={`q-${q.key}`} type="text" value={answers[q.key] ?? ''} onChange={(e) => setAnswers({ ...answers, [q.key]: e.target.value })} />
                )}
              </li>
            ))}
          </ul>
          {live.pending.some((q) => !q.blocked) && (
            <label className="check">
              <input type="checkbox" checked={saveToBank} onChange={(e) => setSaveToBank(e.target.checked)} />
              Save my answers to the answer bank for next time
            </label>
          )}
          <div className="row-buttons">
            {live.pending.some((q) => !q.blocked) && (
              <button
                type="button"
                className="primary"
                disabled={busy || live.pending.some((q) => !q.blocked && !(answers[q.key] ?? '').trim())}
                onClick={() => void act(async () => { await api.answer(live.id, answers, saveToBank) })}
              >
                Answer and continue
              </button>
            )}
            {listing.primary && (
              <a className="button" href={listing.primary.posting_url} target="_blank" rel="noreferrer">
                Open the posting
              </a>
            )}
            {skipButton}
          </div>
        </div>
      )}

      {live?.status === 'ready_for_review' && (
        <div className="review">
          <p className="lead">
            <strong>Ready for review.</strong> Nothing has been sent. Check every answer, edit any of them, then Approve
            and submit.
          </p>
          <ul className="form-answers">
            {live.fields.map((f) => (
              <FieldInput key={f.key} f={f} value={valueOf(f)} onChange={(v) => setEdits({ ...edits, [f.key]: v })} />
            ))}
          </ul>
          <div className="row-buttons">
            <button
              type="button"
              className="primary"
              disabled={busy}
              onClick={() => void act(async () => {
                if (dirty) await api.editAnswers(live.id, edits)
                await api.approve(live.id)
              })}
            >
              {dirty ? 'Save and approve' : 'Approve and submit'}
            </button>
            <button type="button" disabled={busy || !dirty} onClick={() => void act(async () => { await api.editAnswers(live.id, edits) })}>
              Save changes
            </button>
            {skipButton}
          </div>
        </div>
      )}
    </section>
  )
}
