import { useEffect, useRef, useState } from 'react'

import { api, type Resume } from '../api/client'

type Choice = 'must' | 'nice' | 'skip'

type Props = {
  mustHave: string[]
  niceToHave: string[]
  onAccepted: (must: string[], nice: string[]) => void
}

const fmt = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

export default function ResumeCard({ mustHave, niceToHave, onAccepted }: Props) {
  const [versions, setVersions] = useState<Resume[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [started, setStarted] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const load = async () => {
    try {
      setVersions(await api.resumes())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }
  useEffect(() => {
    void load()
  }, [])

  // Poll while a version is still being parsed.
  const parsing = versions.some((v) => v.status === 'parsing')
  useEffect(() => {
    if (!parsing) return
    const timer = setInterval(() => void load(), 2000)
    return () => clearInterval(timer)
  }, [parsing])

  const upload = async (file: File) => {
    setBusy(true)
    setError(null)
    setStarted(false)
    try {
      await api.uploadResume(file)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const active = versions.find((v) => v.is_active)
  const pending = active && active.status === 'parsed' && !active.accepted_at ? active : null

  return (
    <section className="card">
      <div className="card-head">
        <h2>Resume</h2>
        <label className={busy ? 'button primary disabled' : 'button primary'}>
          {busy ? 'Uploading…' : versions.length ? 'Upload new resume' : 'Upload resume'}
          <input
            ref={fileInput}
            type="file"
            accept=".pdf,.docx"
            hidden
            disabled={busy}
            onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])}
          />
        </label>
      </div>
      <p className="hint">PDF or DOCX, up to 10 MB. Each upload is a new version; the newest is attached to applications.</p>
      {error && <p className="error" role="alert">{error}</p>}

      {started && !pending && (
        <p className="saved" role="status">
          Skills saved and the job list re-filtered. A run has started to fetch jobs for them; follow it on the
          Dashboard.
        </p>
      )}
      {active?.status === 'parsing' && <p className="muted">Reading {active.original_name}…</p>}
      {active?.status === 'failed' && (
        <p className="error" role="alert">
          Couldn't read {active.original_name}: {active.parse_error}
        </p>
      )}
      {pending && (
        <Suggestions
          key={pending.id}
          resume={pending}
          mustHave={mustHave}
          niceToHave={niceToHave}
          onAccepted={async (must, nice) => {
            onAccepted(must, nice)
            setStarted(true)
            await load()
          }}
        />
      )}

      {versions.length > 0 && (
        <table className="versions">
          <thead>
            <tr>
              <th scope="col">Version</th>
              <th scope="col">Uploaded</th>
              <th scope="col">Skills</th>
            </tr>
          </thead>
          <tbody>
            {versions.map((v) => (
              <tr key={v.id}>
                <td>
                  <a href={api.resumeFileUrl(v.id)}>{v.original_name}</a>
                  {v.is_active && <span className="badge">Active</span>}
                </td>
                <td>{fmt(v.uploaded_at)}</td>
                <td className="muted">
                  {v.status === 'parsing'
                    ? 'Reading…'
                    : v.status === 'failed'
                      ? 'Failed'
                      : v.accepted_at
                        ? `Accepted ${fmt(v.accepted_at)}`
                        : `${v.suggested_skills.length} suggested`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

type SuggestionsProps = {
  resume: Resume
  mustHave: string[]
  niceToHave: string[]
  onAccepted: (must: string[], nice: string[]) => Promise<void>
}

function Suggestions({ resume, mustHave, niceToHave, onAccepted }: SuggestionsProps) {
  const initial = () => {
    const out: Record<string, Choice> = {}
    for (const s of mustHave) out[s] = 'must'
    for (const s of niceToHave) out[s] = 'nice'
    for (const s of resume.suggested_skills) out[s] ??= 'nice'
    return out
  }
  const [choices, setChoices] = useState<Record<string, Choice>>(initial)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const pick = (skill: string, choice: Choice) => setChoices((c) => ({ ...c, [skill]: choice }))
  const of = (c: Choice) => Object.keys(choices).filter((s) => choices[s] === c)

  const accept = async () => {
    setSaving(true)
    setError(null)
    try {
      await api.acceptResume(resume.id, of('must'), of('nice'))
      await onAccepted(of('must'), of('nice'))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="suggestions">
      <h3>Skills found in {resume.original_name}</h3>
      <p className="hint">
        Nothing changes until you accept. Mark each skill must-have, nice-to-have or skip; these
        replace your current skill lists.
        {resume.titles.length > 0 && ` Titles seen: ${resume.titles.slice(0, 3).join(', ')}.`}
        {resume.years_experience != null && ` About ${resume.years_experience} years of experience.`}
      </p>
      <ul className="skill-picks">
        {Object.keys(choices).map((skill) => (
          <li key={skill} className={`pick ${choices[skill]}`}>
            <span className="skill">
              {skill}
              {!resume.suggested_skills.includes(skill) && <span className="muted"> (current)</span>}
            </span>
            <span className="segmented" role="group" aria-label={`${skill} importance`}>
              {(['must', 'nice', 'skip'] as const).map((c) => (
                <button
                  key={c}
                  type="button"
                  aria-pressed={choices[skill] === c}
                  onClick={() => pick(skill, c)}
                >
                  {c === 'must' ? 'Must-have' : c === 'nice' ? 'Nice-to-have' : 'Skip'}
                </button>
              ))}
            </span>
          </li>
        ))}
      </ul>
      <div className="add-row">
        <input
          aria-label="Add a skill"
          placeholder="Add a skill the resume missed"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && draft.trim()) {
              e.preventDefault()
              pick(draft.trim().toLowerCase(), 'must')
              setDraft('')
            }
          }}
        />
        <button
          type="button"
          onClick={() => {
            if (draft.trim()) pick(draft.trim().toLowerCase(), 'must')
            setDraft('')
          }}
        >
          Add
        </button>
      </div>
      {resume.links_found.length > 0 && (
        <p className="hint">Links found in the resume: {resume.links_found.join(', ')}. Add the ones you want under Contact.</p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="actions">
        <button type="button" className="primary" disabled={saving} onClick={() => void accept()}>
          {saving ? 'Saving…' : `Accept ${of('must').length + of('nice').length} skills`}
        </button>
      </div>
    </div>
  )
}
