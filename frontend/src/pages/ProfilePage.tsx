import { useCallback, useEffect, useRef, useState } from 'react'

import { api, type Answer, type Resume } from '../api/client'
import { errorText, plural, shortDate, when } from '../format'
import { useApp } from '../appContext'
import { useProfileForm } from './profileForm'

type Choice = 'must' | 'nice' | 'skip'

/** Skills found in a new resume: Required, Nice to have or Ignore, then Use these skills (U13). */
function Suggestions({ resume, must, nice, onAccepted }: { resume: Resume; must: string[]; nice: string[]; onAccepted: () => Promise<void> }) {
  const [choices, setChoices] = useState<Record<string, Choice>>(() => {
    const out: Record<string, Choice> = {}
    for (const s of resume.suggested_skills) out[s] = must.includes(s) ? 'must' : 'nice'
    for (const s of must) out[s] ??= 'must'
    for (const s of nice) out[s] ??= 'nice'
    return out
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const of = (c: Choice) => Object.keys(choices).filter((s) => choices[s] === c)
  const fresh = resume.suggested_skills.filter((s) => !must.includes(s) && !nice.includes(s))
  const accept = async () => {
    setSaving(true)
    setError(null)
    try {
      await api.acceptResume(resume.id, of('must'), of('nice'))
      await onAccepted()
    } catch (err) {
      setError(errorText(err))
      setSaving(false)
    }
  }
  return (
    <div className="note-box" style={{ display: 'grid', gap: 10 }}>
      <span>
        <b>{plural(resume.suggested_skills.length, 'skill')} found in this resume</b>
        {fresh.length ? `, ${fresh.length} of them new` : ''}. Choose which ones JAP should treat as required or nice to have. Nothing changes until you press Use these skills.
        {resume.titles.length > 0 && ` Titles seen: ${resume.titles.slice(0, 3).join(', ')}.`}
        {resume.years_experience != null && ` About ${resume.years_experience} years of experience.`}
      </span>
      <div className="inline" style={{ gap: 8 }}>
        {Object.keys(choices).map((s) => (
          <span key={s} className="inline" style={{ gap: 4 }}>
            <span className="tag static">{s}</span>
            <select aria-label={`How JAP treats ${s}`} style={{ width: 'auto', minHeight: 28, padding: '2px 6px' }} value={choices[s]} onChange={(e) => setChoices({ ...choices, [s]: e.target.value as Choice })}>
              <option value="nice">Nice to have</option>
              <option value="must">Required</option>
              <option value="skip">Ignore</option>
            </select>
          </span>
        ))}
      </div>
      <div className="inline">
        <button type="button" className="btn-primary" disabled={saving} onClick={() => void accept()}>
          {saving ? 'Saving…' : 'Use these skills'}
        </button>
        <span>Saving starts a search for jobs that fit them.</span>
      </div>
      {error && <span className="error-text">{error}</span>}
    </div>
  )
}

function ResumeSection({ onLinks }: { onLinks: (links: string[]) => void }) {
  const { profile, setProfile, refresh, toast } = useApp()
  const [versions, setVersions] = useState<Resume[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const load = useCallback(async () => setVersions(await api.resumes()), [])
  useEffect(() => {
    load().catch((err) => setError(errorText(err)))
  }, [load])
  const parsing = versions.some((v) => v.status === 'parsing')
  useEffect(() => {
    if (!parsing) return
    const t = setInterval(() => void load().catch(() => undefined), 2000)
    return () => clearInterval(t)
  }, [parsing, load])
  const active = versions.find((v) => v.is_active)
  useEffect(() => onLinks(active?.links_found ?? []), [active?.links_found, onLinks])

  const upload = async (file: File) => {
    setBusy(true)
    setError(null)
    try {
      await api.uploadResume(file)
      await load()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
      if (input.current) input.current.value = ''
    }
  }
  const pending = active && active.status === 'parsed' && !active.accepted_at ? active : null
  const earlier = versions.filter((v) => !v.is_active)
  return (
    <div className="sec">
      <div className="sec-l">
        <h2>Resume</h2>
        <p>Attached to every application. JAP reads your skills from it. PDF or Word, up to 10 MB.</p>
      </div>
      <div className="sec-r">
        <div className="inline spread">
          {active ? (
            <span>
              <a href={api.resumeFileUrl(active.id)} target="_blank" rel="noreferrer">
                <b>{active.original_name}</b>
              </a>{' '}
              <span className="muted">· uploaded {shortDate(active.uploaded_at)}</span>
            </span>
          ) : (
            <span className="muted">No resume yet. JAP needs one before it can apply.</span>
          )}
          <label className={busy ? 'btn btn-primary' : active ? 'btn' : 'btn btn-primary'} style={{ cursor: 'pointer' }}>
            {busy ? 'Uploading…' : active ? 'Upload new version' : 'Upload resume'}
            <input ref={input} type="file" accept=".pdf,.docx" hidden disabled={busy} onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])} />
          </label>
        </div>
        {error && <p className="note-box bad">{error}</p>}
        {active?.status === 'parsing' && <p className="note-box">Reading {active.original_name}…</p>}
        {active?.status === 'failed' && <p className="note-box bad">Couldn&apos;t read {active.original_name}: {active.parse_error}</p>}
        {pending && profile && (
          <Suggestions
            key={pending.id}
            resume={pending}
            must={profile.must_have_skills}
            nice={profile.nice_to_have_skills}
            onAccepted={async () => {
              setProfile(await api.profile())
              await load()
              void refresh()
              toast('Skills saved. JAP is searching for jobs that fit them.')
            }}
          />
        )}
        {active?.status === 'parsed' && active.suggested_skills.length > 0 && (
          <div className="f">
            <span className="lab">Skills found in your resume</span>
            <div className="tags bare">
              {active.suggested_skills.map((s) => (
                <span key={s} className="tag static">
                  {s}
                </span>
              ))}
            </div>
            <span className="help">
              Used to write cover letters and answers. Which skills are required or nice to have is set in{' '}
              <a href="#prefs">Job preferences</a>.
            </span>
          </div>
        )}
        {earlier.length > 0 && (
          <details>
            <summary className="muted">Earlier versions ({earlier.length})</summary>
            <table className="versions">
              <tbody>
                {earlier.map((v) => (
                  <tr key={v.id}>
                    <td>
                      <a href={api.resumeFileUrl(v.id)} target="_blank" rel="noreferrer">
                        {v.original_name}
                      </a>
                    </td>
                    <td className="mono">{shortDate(v.uploaded_at)}</td>
                    <td className="muted">{v.status === 'failed' ? "Couldn't be read" : v.accepted_at ? `Skills used ${shortDate(v.accepted_at)}` : 'Skills not used'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        )}
      </div>
    </div>
  )
}

function SavedAnswers() {
  const [answers, setAnswers] = useState<Answer[]>([])
  const [editing, setEditing] = useState<Answer | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => setAnswers(await api.answers()), [])
  useEffect(() => {
    load().catch((err) => setError(errorText(err)))
  }, [load])
  const run = async (fn: () => Promise<unknown>) => {
    setError(null)
    try {
      await fn()
      await load()
      setEditing(null)
    } catch (err) {
      setError(errorText(err))
    }
  }
  const editor = (a: Answer) => (
    <div key={a.id || 'new'} className="qa editing">
      <div className="f">
        <label htmlFor="ans-q">Question</label>
        <input id="ans-q" type="text" placeholder="e.g. What is your notice period?" value={a.question} onChange={(e) => setEditing({ ...a, question: e.target.value })} />
      </div>
      <div className="f">
        <label htmlFor="ans-a">Answer</label>
        <textarea id="ans-a" rows={2} value={a.answer} onChange={(e) => setEditing({ ...a, answer: e.target.value })} />
      </div>
      <div className="inline">
        <button type="button" className="btn-primary" disabled={!a.question.trim() || !a.answer.trim()} onClick={() => void run(() => (a.id ? api.editAnswer(a) : api.addAnswer({ question: a.question, answer: a.answer })))}>
          Save answer
        </button>
        <button type="button" onClick={() => setEditing(null)}>
          Cancel
        </button>
        {a.id && (
          <button type="button" className="btn-quiet btn-danger" onClick={() => void run(() => api.removeAnswer(a.id))}>
            Remove
          </button>
        )}
      </div>
    </div>
  )
  return (
    <div className="sec">
      <div className="sec-l">
        <h2>Saved answers</h2>
        <p>Reused when a form asks the same question again, such as notice period, salary or work permit. Answers you type for a job are saved here.</p>
      </div>
      <div className="sec-r">
        {answers.length === 0 && !editing && <p className="muted">No saved answers yet.</p>}
        {answers.length > 0 && (
          <div>
            {answers.map((a) =>
              editing?.id === a.id ? (
                editor(editing)
              ) : (
                <div key={a.id} className="qa">
                  <span>{a.question}</span>
                  <span className="muted a">{a.answer}</span>
                  <button type="button" aria-label={`Edit the answer to ${a.question}`} onClick={() => setEditing(a)}>
                    Edit
                  </button>
                </div>
              ),
            )}
          </div>
        )}
        {editing && !editing.id ? editor(editing) : (
          <div className="inline">
            <button type="button" onClick={() => setEditing({ id: '', question: '', answer: '' })}>
              Add an answer
            </button>
          </div>
        )}
        {error && <p className="note-box bad">{error}</p>}
      </div>
    </div>
  )
}

export default function ProfilePage() {
  const { form, set, dirty, saving, error, save, discard } = useProfileForm()
  const { toast } = useApp()
  const [found, setFound] = useState<string[]>([])
  const onLinks = useCallback((l: string[]) => setFound(l), [])
  const [savedAt, setSavedAt] = useState<string | null>(null)

  const links = form?.links ?? []
  const unused = found.filter((u) => !links.some((l) => l.url.replace(/\/$/, '') === u.replace(/\/$/, '')))
  const labelFor = (url: string) => {
    const host = url.replace(/^https?:\/\//, '').split('/')[0].replace(/^www\./, '')
    return host.includes('linkedin') ? 'LinkedIn' : host.includes('github') ? 'GitHub' : host.split('.')[0].replace(/^./, (c) => c.toUpperCase())
  }

  return (
    <section className="page">
      <div className="page-head">
        <div>
          <h1>Profile</h1>
          <p>What JAP sends to employers. Nothing here changes which jobs you see.</p>
        </div>
      </div>
      <ResumeSection onLinks={onLinks} />
      {form && (
        <>
          <div className="sec">
            <div className="sec-l">
              <h2>Contact details</h2>
              <p>Filled into application forms exactly as written. Never sent to an AI model.</p>
            </div>
            <div className="sec-r">
              <div className="two">
                <div className="f">
                  <label htmlFor="p-name">Full name</label>
                  <input id="p-name" type="text" autoComplete="name" value={form.full_name ?? ''} onChange={(e) => set('full_name', e.target.value || null)} />
                </div>
                <div className="f">
                  <label htmlFor="p-email">Email</label>
                  <input id="p-email" type="email" autoComplete="email" value={form.email ?? ''} onChange={(e) => set('email', e.target.value || null)} />
                  <span className="help">Confirmation emails go here too.</span>
                </div>
                <div className="f">
                  <label htmlFor="p-phone">Phone</label>
                  <input id="p-phone" type="tel" autoComplete="tel" value={form.phone ?? ''} onChange={(e) => set('phone', e.target.value || null)} />
                </div>
                <div className="f">
                  <label htmlFor="p-address">Address</label>
                  <input id="p-address" type="text" autoComplete="street-address" value={form.address ?? ''} onChange={(e) => set('address', e.target.value || null)} />
                </div>
              </div>
              <div className="f">
                <span className="lab">Links</span>
                {links.length === 0 && <span className="help">LinkedIn, GitHub, portfolio or website.</span>}
                {links.map((link, i) => (
                  <div key={i} className="links-row">
                    <input type="text" aria-label="Link name" placeholder="LinkedIn" value={link.label} onChange={(e) => set('links', links.map((l, j) => (j === i ? { ...l, label: e.target.value } : l)))} />
                    <input type="url" aria-label="Link address" placeholder="https://" value={link.url} onChange={(e) => set('links', links.map((l, j) => (j === i ? { ...l, url: e.target.value } : l)))} />
                    <button type="button" className="btn-quiet" aria-label={`Remove ${link.label || 'link'}`} onClick={() => set('links', links.filter((_, j) => j !== i))}>
                      Remove
                    </button>
                  </div>
                ))}
                <div className="inline">
                  <button type="button" onClick={() => set('links', [...links, { label: '', url: '' }])}>
                    Add a link
                  </button>
                  {unused.length > 0 && (
                    <span className="suggest">
                      Found in your resume:
                      {unused.map((u) => (
                        <button key={u} type="button" onClick={() => set('links', [...links, { label: labelFor(u), url: u.startsWith('http') ? u : `https://${u}` }])}>
                          + {u.replace(/^https?:\/\//, '')}
                        </button>
                      ))}
                    </span>
                  )}
                </div>
              </div>
            </div>
          </div>
          <SavedAnswers />
          <div className="sec">
            <div className="sec-l">
              <h2>Cover letter</h2>
              <p>How JAP should write your letters, when a form asks for one.</p>
            </div>
            <div className="sec-r">
              <div className="f">
                <label htmlFor="p-tone">Style</label>
                <input id="p-tone" type="text" maxLength={500} placeholder="Short and direct, no buzzwords" value={form.tone_note ?? ''} onChange={(e) => set('tone_note', e.target.value || null)} />
                <span className="help">For example: “formal”, “mention open-source work first”.</span>
              </div>
            </div>
          </div>
        </>
      )}
      {(dirty || error || savedAt) && (
        <div className="savebar">
          <div className="in">
            {error ? (
              <span className="error-text" role="alert">
                {error}
              </span>
            ) : dirty ? (
              <span className="muted">Unsaved changes in Contact details or Cover letter.</span>
            ) : (
              <span className="muted" role="status">
                Saved {savedAt}.
              </span>
            )}
            <span className="inline">
              <button type="button" disabled={!dirty || saving} onClick={discard}>
                Discard
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={!dirty || saving}
                onClick={() =>
                  void save().then((n) => {
                    if (n === null) return
                    setSavedAt(when(new Date().toISOString()))
                    toast('Profile saved.')
                  })
                }
              >
                {saving ? 'Saving…' : 'Save changes'}
              </button>
            </span>
          </div>
        </div>
      )}
    </section>
  )
}
