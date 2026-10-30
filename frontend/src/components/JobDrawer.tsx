import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'

import { api, type Application, type CoverLetter, type FormField, type ListingDetail, type Profile, type Salary } from '../api/client'
import { atsLabel, errorText, plural, shortDate, STATUS, when, workModeLabel } from '../format'
import { useApp } from '../appContext'

type Props = {
  id: string
  onClose: () => void
  onOpen: (id: string) => void
  /** Apply opens the confirmation in the Jobs page, the same one bulk Apply uses. */
  onApply: (listing: ListingDetail) => void
}

const LETTER = /cover ?letter/i
/** A mandatory field that takes the cover letter itself (pipeline/apply/fields.py). */
const isLetterField = (f: FormField) =>
  f.required && ['file', 'text', 'textarea'].includes(f.type) && (LETTER.test(f.label) || LETTER.test(f.key))
const MAX_WORDS = 250
const countWords = (t: string) => t.split(/\s+/).filter(Boolean).length
const MAYBE_SENT =
  "JAP pressed Submit on this job last time and couldn't tell whether it went through. Check the employer's site or your email first.\n\nPress OK only if it wasn't sent."

/** Where an answer came from, as a tag beside it (U10). */
function source(from: string): [string, string] | null {
  if (from.startsWith('profile:')) return ['From profile', '']
  if (from === 'resume') return ['From resume', '']
  if (from === 'answer_bank') return ['Saved answer', '']
  if (from === 'drafted' || from === 'cover_letter') return ['Written by AI', 'ai']
  if (from === 'you') return ['Your answer', 'you']
  return null
}

const money = (n: number, currency: string | null) =>
  new Intl.NumberFormat(undefined, { style: currency ? 'currency' : 'decimal', currency: currency ?? undefined, maximumFractionDigits: 0 }).format(n)

function salaryText(s: Salary | undefined): string | null {
  if (!s || (s.min == null && s.max == null)) return null
  const range = [s.min, s.max].filter((v): v is number => v != null).map((v) => money(v, s.currency)).join(' – ')
  return s.period ? `${range} a ${s.period}` : range
}

function Sec({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <p className="label-sm">{title}</p>
      {children}
    </div>
  )
}

function Details({ listing }: { listing: ListingDetail }) {
  const job = listing.primary
  const f = job?.extracted ? job.fields : null
  const rows: [string, ReactNode][] = [
    ['Work mode', listing.work_mode ? workModeLabel(listing.work_mode) : <span className="mode-unclear">Not stated</span>],
    ['Posted', shortDate(listing.last_posted_at)],
  ]
  if (f) {
    const salary = salaryText(f.salary)
    if (salary) rows.push(['Salary', salary])
    if (f.skills?.length) rows.push(['Skills asked', f.skills.join(', ')])
    if (f.seniority && f.seniority !== 'unknown') rows.push(['Seniority', f.seniority])
    if (f.remote_regions?.length) rows.push(['Remote from', f.remote_regions.join(', ')])
    if (f.visa_sponsorship && f.visa_sponsorship !== 'unknown') rows.push(['Visa sponsorship', f.visa_sponsorship])
    if (f.cover_letter_asked != null) rows.push(['Cover letter', f.cover_letter_asked ? 'Asked for' : 'Not asked for'])
  }
  if (job?.department) rows.push(['Team', job.department])
  if (job?.employment_type) rows.push(['Type', job.employment_type])
  rows.push(['Found on', listing.platforms.map(atsLabel).join(', ') || '–'])
  return (
    <Sec title="Job details">
      <dl className="kv">
        {rows.map(([k, v]) => (
          <div key={k} style={{ display: 'contents' }}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      {job && !job.extracted && <p className="help">JAP is still reading this description for skills, salary and work mode…</p>}
    </Sec>
  )
}

function WhyItFits({ listing, profile }: { listing: ListingDetail; profile: Profile | null }) {
  const items: [string, boolean][] = []
  const m = listing.matched
  items.push(m.includes('role') ? [`Title fits ${listing.matched_role ?? 'one of your job titles'}`, true] : ["Title isn't one of your job titles", false])
  if (m.includes('location')) {
    const regions = listing.remote_regions.length ? listing.remote_regions.map((r) => (/worldwide|anywhere|global/i.test(r) ? 'anywhere' : r)).join(', ') : 'anywhere'
    const office = listing.locations.find((l) => !/remote/i.test(l))
    items.push([listing.work_mode === 'remote' || !office ? `Remote job open to ${regions}` : `Office in ${office}`, true])
  } else {
    items.push(["Location isn't one of yours", false])
  }
  const required = profile?.must_have_skills ?? []
  const asked = required.filter((s) => listing.skills.includes(s))
  const missing = required.filter((s) => !listing.skills.includes(s))
  if (asked.length) items.push([`Asks for ${asked.join(', ')}, from your required skills`, true])
  if (missing.length && listing.skills.length) items.push([`Doesn't ask for ${missing.join(', ')}`, false])
  if (listing.nice_skills.length) items.push([`Also asks for ${listing.nice_skills.join(', ')}, nice to have`, true])
  if (!listing.work_mode) items.push(['Work mode not stated, so JAP never sends it on its own', false])
  return (
    <Sec title="Why it fits">
      <ul className="checks">
        {items.map(([t, ok]) => (
          <li key={t} className={ok ? '' : 'miss'}>
            {t}
          </li>
        ))}
      </ul>
    </Sec>
  )
}

/** Other boards with this job. JAP joins and settles duplicates itself (PRD 1.7); Not the
 * same job is only for the rare wrong join. */
function AlsoOn({ listing, busy, act, onOpen }: { listing: ListingDetail; busy: boolean; act: (fn: () => Promise<unknown>) => void; onOpen: (id: string) => void }) {
  if (listing.also_on.length === 0) return null
  return (
    <Sec title="Also posted on">
      <div className="alsoon">
        {listing.also_on.map((a) => (
          <div key={a.posting_id}>
            <span style={{ minWidth: 0 }}>
              <a href={a.posting_url} target="_blank" rel="noreferrer">
                {atsLabel(a.source)}
              </a>{' '}
              · {a.title === listing.title ? 'same title' : a.title}, {a.locations.join(' · ') || 'no location given'}
              {a.state === 'closed' && <span className="muted"> · closed</span>}
            </span>
            <button
              type="button"
              className="btn-link"
              disabled={busy}
              title="JAP joined these by mistake: make this posting a job of its own (remembered on later searches)"
              onClick={() => act(async () => onOpen((await api.split(a.posting_id)).listing_id))}
            >
              Not the same job
            </button>
          </div>
        ))}
      </div>
    </Sec>
  )
}

function Description({ listing }: { listing: ListingDetail }) {
  const app = listing.application
  const frozen = app?.status === 'applied' ? app.jd : null
  if (frozen) {
    return (
      <Sec title="Description, as it was when you applied">
        <p className="help">Saved {frozen.captured_at ? when(frozen.captured_at) : 'when you applied'}.</p>
        <div className="jd">{frozen.text}</div>
      </Sec>
    )
  }
  if (!listing.primary) return null
  return (
    <Sec title="Description">
      <div className="jd">{listing.primary.description_text}</div>
    </Sec>
  )
}

/** One form answer while Ready to send: every answer is shown and editable. */
function FieldInput({ f, value, onChange }: { f: FormField; value: string; onChange: (v: string) => void }) {
  const id = `field-${f.key}`
  let input
  if (isLetterField(f) && value) {
    input = <span className="help">{f.type === 'file' ? `Uploads ${value}, made from the cover letter below` : 'Pastes the cover letter below'}</span>
  } else if (f.type === 'file') {
    input = <span className="help">{value || 'Nothing attached'}</span>
  } else if (f.type === 'textarea' || value.length > 80) {
    input = <textarea id={id} rows={Math.min(10, Math.max(3, Math.ceil(value.length / 60)))} maxLength={f.max_chars ?? undefined} value={value} onChange={(e) => onChange(e.target.value)} />
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
  const src = source(f.filled_from)
  return (
    <div className="f">
      <div className="qh">
        <label htmlFor={id}>
          {f.label}
          {f.required && <span className="req" aria-label="required"> *</span>}
        </label>
        {src && <span className={`src ${src[1]}`}>{src[0]}</span>}
      </div>
      {input}
      {f.max_chars && f.type !== 'file' && <span className="help">{value.length} of {f.max_chars} characters</span>}
    </div>
  )
}

function Claims({ letter }: { letter: CoverLetter }) {
  if (letter.claims.length === 0) return null
  const found = letter.claims.filter((c) => c.found).length
  return (
    <details className="claims">
      <summary>
        <span className={`pill ${found === letter.claims.length ? 's-sent' : 's-fail'}`}>
          {found} of {letter.claims.length} claims found in your resume
        </span>
      </summary>
      <ul>
        {letter.claims.map((c, i) => (
          <li key={i} className={c.found ? '' : 'miss'}>
            {c.claim}
            {c.evidence && <span className="muted"> · resume: “{c.evidence}”</span>}
          </li>
        ))}
      </ul>
    </details>
  )
}

function Shot({ app, alt }: { app: Application; alt: string }) {
  return (
    <a className="shot" href={api.fileUrl('screenshot', app.id)} target="_blank" rel="noreferrer">
      <img src={api.fileUrl('screenshot', app.id)} alt={alt} />
    </a>
  )
}

export default function JobDrawer({ id, onClose, onOpen, onApply }: Props) {
  const { profile, refresh, toast } = useApp()
  const [listing, setListing] = useState<ListingDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [letterEdit, setLetterEdit] = useState<string | null>(null)
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [saveToBank, setSaveToBank] = useState(true)
  const [queued, setQueued] = useState(false)
  const closeRef = useRef<HTMLButtonElement>(null)

  const load = useCallback(() => api.listing(id).then(setListing, (err) => setError(errorText(err))), [id])
  useEffect(() => {
    setListing(null)
    setError(null)
    void load()
  }, [load])
  useEffect(() => {
    closeRef.current?.focus({ focusVisible: false } as FocusOptions)
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const app = listing?.application ?? null
  const appKey = `${app?.id}:${app?.status}`
  useEffect(() => {
    setEdits({})
    setLetterEdit(null)
    setAnswers({})
    setQueued(false)
  }, [appKey])
  // While the description is still being read, or JAP is filling or sending the form, check again.
  useEffect(() => {
    if (!listing) return
    const working = queued || listing.status === 'applying' || app?.status === 'preparing'
    if (!working && (!listing.primary || listing.primary.extracted)) return
    const t = setTimeout(() => void load(), working ? 2000 : 5000)
    return () => clearTimeout(t)
  }, [listing, app?.status, queued, load])

  const act = async (fn: () => Promise<unknown>, done?: string) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await load()
      void refresh()
      if (done) toast(done)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }
  const hide = (verb: 'Hidden' | 'Skipped') => {
    if (!listing) return
    const lid = listing.id
    void act(async () => {
      await api.skip(lid)
      toast({
        text: `${verb}. Find it again with Show hidden and skipped jobs.`,
        undo: () => void api.unskip(lid).then(() => refresh()),
      })
      onClose()
    })
  }

  const status = listing?.status
  const live = app && ['preparing', 'ready_for_review', 'applying', 'needs_input'].includes(app.status) ? app : null
  const letterDirty = letterEdit !== null && letterEdit.trim() !== (app?.cover_letter?.text ?? '')
  const dirty = Object.keys(edits).length > 0 || letterDirty
  const saveEdits = (appId: string) => api.editAnswers(appId, edits, letterDirty ? (letterEdit ?? undefined) : undefined)
  const openLink = listing?.primary && (
    <a className="btn btn-quiet open-link" href={listing.primary.posting_url} target="_blank" rel="noreferrer">
      Open job page ↗
    </a>
  )
  const pill = listing && (app?.status === 'preparing' ? ['Preparing', 's-ready'] : STATUS[listing.status])

  let actions: ReactNode = openLink
  let body: ReactNode = null
  if (listing) {
    const common = (
      <>
        <Details listing={listing} />
        <WhyItFits listing={listing} profile={profile} />
        <AlsoOn listing={listing} busy={busy} act={(fn) => void act(fn)} onOpen={onOpen} />
      </>
    )
    if (live?.status === 'preparing' || (queued && !live)) {
      body = (
        <>
          <div className="note-box" role="status">
            JAP is opening the form and filling it in from your profile, resume and saved answers
            {listing.primary?.fields?.cover_letter_asked ? ', and writing a cover letter' : ''}…
          </div>
          {common}
          <Description listing={listing} />
        </>
      )
    } else if (live?.status === 'applying') {
      body = (
        <>
          <div className="note-box" role="status">
            Sending the application… A screenshot of the confirmation follows.
          </div>
          {common}
        </>
      )
    } else if (live?.status === 'needs_input') {
      const open = live.pending.filter((q) => !q.blocked)
      const ready = open.length > 0 && open.every((q) => (answers[q.key] ?? '').trim())
      actions = (
        <>
          {open.length > 0 && (
            <button type="button" className="btn-primary" disabled={busy || !ready} onClick={() => void act(() => api.answer(live.id, answers, saveToBank), 'Answer saved. JAP is finishing the form.')}>
              Save and continue
            </button>
          )}
          <button type="button" disabled={busy} onClick={() => hide('Skipped')}>
            Skip this job
          </button>
          {openLink}
        </>
      )
      const draftFailed = live.pending.some((q) => q.draft_failed)
      body = (
        <>
          <div className="note-box warn">
            {draftFailed
              ? "JAP couldn't write an answer here because the AI model failed (the reason is under the question). Write your own, or skip and apply again later."
              : `JAP stopped at ${live.pending.length === 1 ? 'a question' : 'questions'} only you can answer. Nothing has been sent.`}
          </div>
          <Sec title={live.pending.length === 1 ? 'Your answer is needed' : 'Your answers are needed'}>
            <div className="qform">
              {live.pending.map((q) => {
                const set = (v: string) => setAnswers({ ...answers, [q.key]: v })
                const qid = `q-${q.key}`
                return (
                  <div key={q.key} className="f">
                    <div className="qh">
                      <label htmlFor={qid}>
                        <b>{q.label}</b>
                      </label>
                      <span className="src">Required</span>
                    </div>
                    <span className="help">{q.reason}</span>
                    {q.blocked ? null : q.options.length > 0 ? (
                      <select id={qid} value={answers[q.key] ?? ''} onChange={(e) => set(e.target.value)}>
                        <option value="">Choose…</option>
                        {q.options.map((o) => (
                          <option key={o} value={o}>
                            {o}
                          </option>
                        ))}
                      </select>
                    ) : LETTER.test(q.label) || q.draft_failed ? (
                      <textarea id={qid} rows={LETTER.test(q.label) ? 10 : 5} placeholder={LETTER.test(q.label) ? 'Paste your cover letter' : undefined} value={answers[q.key] ?? ''} onChange={(e) => set(e.target.value)} />
                    ) : (
                      <input id={qid} type="text" value={answers[q.key] ?? ''} onChange={(e) => set(e.target.value)} />
                    )}
                  </div>
                )
              })}
              {open.some((q) => !LETTER.test(q.label)) && (
                <label className="inline small">
                  <input type="checkbox" checked={saveToBank} onChange={(e) => setSaveToBank(e.target.checked)} /> Save to Saved answers for next time
                </label>
              )}
            </div>
          </Sec>
          {common}
          <Description listing={listing} />
        </>
      )
    } else if (live?.status === 'ready_for_review') {
      const letter = live.cover_letter
      const text = letterEdit ?? letter?.text ?? ''
      actions = (
        <>
          <button
            type="button"
            className="btn-primary"
            disabled={busy}
            onClick={() =>
              void act(async () => {
                if (dirty) await saveEdits(live.id)
                await api.approve(live.id)
              }, 'Sending your application. A confirmation email follows.')
            }
          >
            {dirty ? 'Save and send' : 'Send application'}
          </button>
          {dirty && (
            <button type="button" disabled={busy} onClick={() =>
                void act(async () => {
                  await saveEdits(live.id)
                  setEdits({})
                  setLetterEdit(null)
                }, 'Changes saved. Nothing has been sent.')
              }>
              Save changes
            </button>
          )}
          <button type="button" disabled={busy} onClick={() => hide('Skipped')}>
            Skip this job
          </button>
          {openLink}
        </>
      )
      body = (
        <>
          <div className="note-box">Ready to send. Nothing has been sent yet. Check the answers{letter ? ' and the letter' : ''}, edit anything, then press Send application.</div>
          {live.review_reason && <div className="note-box warn">JAP applies for you, but left this one for you: {live.review_reason}.</div>}
          <Sec title="Form answers">
            <div className="qform">
              {live.fields.map((f) => (
                <FieldInput key={f.key} f={f} value={edits[f.key] ?? f.value} onChange={(v) => setEdits({ ...edits, [f.key]: v })} />
              ))}
            </div>
          </Sec>
          {letter && (
            <Sec title="Cover letter">
              <div className="qform">
                <div className="qh">
                  <label htmlFor="cover-letter" className="sr-only">
                    Cover letter
                  </label>
                  <span className={`src ${letter.source === 'you' ? 'you' : 'ai'}`}>{letter.source === 'you' ? 'Edited by you' : 'Written by AI'}</span>
                </div>
                <textarea id="cover-letter" rows={12} value={text} onChange={(e) => setLetterEdit(e.target.value)} />
                <span className="inline small">
                  <Claims letter={letter} />
                  <span className={countWords(text) >= MAX_WORDS ? 'error-text' : 'muted'}>
                    {countWords(text)} words (under {MAX_WORDS})
                  </span>
                  <span className="muted">{letter.as_file ? 'Will be uploaded as a PDF' : 'Will be pasted into the form'}</span>
                  {letter.has_pdf && (
                    <a href={`${api.fileUrl('cover_letter', live.id)}?v=${encodeURIComponent(letter.text.length + ':' + letter.words)}`} target="_blank" rel="noreferrer">
                      View PDF
                    </a>
                  )}
                </span>
              </div>
            </Sec>
          )}
          {common}
          <Description listing={listing} />
        </>
      )
    } else if (status === 'applied' && app?.status === 'applied') {
      body = (
        <>
          <div className="note-box good">
            Sent {app.submitted_at ? when(app.submitted_at) : ''}
            {app.jd ? ` through ${atsLabel(app.jd.source)}` : ''}.{' '}
            {app.email_sent_at ? `A confirmation email went to you at ${when(app.email_sent_at)}.` : 'The confirmation email has not gone out yet.'}
          </div>
          <Sec title="What was sent">
            <dl className="kv">
              <dt>Resume</dt>
              <dd>
                {app.resume ? (
                  <>
                    <a href={api.resumeFileUrl(app.resume.id)} target="_blank" rel="noreferrer">
                      {app.resume.original_name}
                    </a>
                    <span className="muted"> · uploaded {shortDate(app.resume.uploaded_at)}</span>
                  </>
                ) : (
                  '–'
                )}
              </dd>
              <dt>Cover letter</dt>
              <dd>
                {app.cover_letter_text ? (
                  <>
                    {app.cover_letter?.as_file ? 'Uploaded as a PDF' : 'Pasted into the form'}
                    {app.cover_letter?.source === 'you' ? ', with your edits' : ''}
                    {app.cover_letter?.has_pdf && (
                      <>
                        {' · '}
                        <a href={api.fileUrl('cover_letter', app.id)} target="_blank" rel="noreferrer">
                          View
                        </a>
                      </>
                    )}
                  </>
                ) : (
                  'None asked for'
                )}
              </dd>
            </dl>
          </Sec>
          <Sec title="Form answers">
            <dl className="kv">
              {app.fields
                .filter((f) => f.value && !isLetterField(f))
                .map((f) => (
                  <div key={f.key} style={{ display: 'contents' }}>
                    <dt>{f.label}</dt>
                    <dd>{f.value}</dd>
                  </div>
                ))}
            </dl>
          </Sec>
          {app.cover_letter_text && (
            <Sec title="Cover letter as sent">
              <div className="jd">{app.cover_letter_text}</div>
            </Sec>
          )}
          {app.has_screenshot && (
            <Sec title="Confirmation screenshot">
              <Shot app={app} alt="The page the employer showed after the application was sent" />
            </Sec>
          )}
          <Details listing={listing} />
          <Description listing={listing} />
        </>
      )
    } else if (status === 'failed' && app) {
      actions = (
        <>
          <button
            type="button"
            className="btn-primary"
            disabled={busy || queued}
            onClick={() => {
              if (app.submit_pressed && !window.confirm(MAYBE_SENT)) return
              void act(async () => {
                await api.retry(app.id, app.submit_pressed)
                setQueued(true)
              }, 'Trying again…')
            }}
          >
            Try again
          </button>
          {listing.primary && (
            <a className="btn" href={listing.primary.apply_url || listing.primary.posting_url} target="_blank" rel="noreferrer">
              Apply yourself ↗
            </a>
          )}
          <button type="button" disabled={busy} onClick={() => hide('Hidden')}>
            Hide
          </button>
        </>
      )
      body = (
        <>
          <div className="note-box bad">
            <b>Couldn't send.</b> {app.error ?? 'The reason was not recorded.'}
            {app.submit_pressed && ' JAP pressed Submit, so check your email or the employer’s site before trying again.'}
          </div>
          {app.has_screenshot && (
            <Sec title="Where it stopped">
              <Shot app={app} alt="The page when the application stopped" />
            </Sec>
          )}
          {app.has_trace && (
            <p className="help">
              A step-by-step recording is saved too:{' '}
              <a href={api.fileUrl('trace', app.id)} target="_blank" rel="noreferrer">
                download it
              </a>{' '}
              and open it at trace.playwright.dev.
            </p>
          )}
          {common}
          <Description listing={listing} />
        </>
      )
    } else if (status === 'skipped') {
      actions = (
        <>
          <button type="button" disabled={busy} onClick={() => void act(() => api.unskip(listing.id), 'Shown again in Jobs.')}>
            Undo hide
          </button>
          {openLink}
        </>
      )
      body = (
        <>
          <div className="note-box plain">You hid this job. It stays out of the list until you undo it.</div>
          {common}
          <Description listing={listing} />
        </>
      )
    } else {
      actions = (
        <>
          {status !== 'closed' && (
            <>
              <button type="button" className="btn-primary" disabled={busy || !!listing.apply_blocked} onClick={() => onApply(listing)}>
                Apply
              </button>
              <button type="button" disabled={busy} onClick={() => hide('Hidden')}>
                Hide
              </button>
            </>
          )}
          {openLink}
        </>
      )
      body = (
        <>
          {status === 'closed' && <div className="note-box plain">This job is no longer open on any board.</div>}
          {listing.apply_blocked && status !== 'closed' && <div className="note-box warn">JAP can't apply here: {listing.apply_blocked}.</div>}
          {app?.status === 'cancelled' && listing.attempts > 0 && <p className="help">An earlier application was cancelled when you skipped the job. Apply starts a new one.</p>}
          {common}
          <Description listing={listing} />
        </>
      )
    }
  }

  return (
    <div className="drawer-bg" onClick={onClose}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <div className="inline spread">
            <span>{pill && <span className={`pill ${pill[1]}`}>{pill[0]}</span>}</span>
            <button ref={closeRef} type="button" className="btn-quiet" onClick={onClose}>
              Close
            </button>
          </div>
          <h1 id="drawer-title">{listing?.title ?? 'Loading…'}</h1>
          {listing && (
            <p className="muted">
              {listing.company} · {listing.locations.join(' · ') || 'No location given'}
              {listing.postings > 1 && ` · on ${plural(listing.platforms.length || listing.postings, 'board')}`}
            </p>
          )}
          {listing && <div className="inline">{actions}</div>}
          {error && (
            <p className="error-text" role="alert">
              {error}
            </p>
          )}
        </div>
        <div className="drawer-body">{body}</div>
      </aside>
    </div>
  )
}
