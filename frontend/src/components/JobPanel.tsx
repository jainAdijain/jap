import { useEffect, useRef, useState } from 'react'

import { api, type Extracted, type PostingDetail, type Salary } from '../api/client'
import { atsLabel, shortDate } from '../format'

const money = (n: number, currency: string | null) =>
  new Intl.NumberFormat(undefined, {
    style: currency ? 'currency' : 'decimal',
    currency: currency ?? undefined,
    maximumFractionDigits: 0,
  }).format(n)

function salaryText(s: Salary | undefined): string | null {
  if (!s || (s.min == null && s.max == null)) return null
  const range = [s.min, s.max].filter((v): v is number => v != null).map((v) => money(v, s.currency)).join(' – ')
  return s.period ? `${range} a ${s.period}` : range
}

function Fields({ f }: { f: Extracted }) {
  const rows: [string, string | null, boolean][] = [
    ['Seniority', f.seniority ?? null, false],
    ['Work mode', f.work_mode ?? null, f.direct.includes('work_mode')],
    ['Salary', salaryText(f.salary), f.direct.includes('salary')],
    ['Remote regions', f.remote_regions?.length ? f.remote_regions.join(', ') : null, false],
    ['Visa sponsorship', f.visa_sponsorship ?? null, false],
    ['Cover letter asked', f.cover_letter_asked == null ? null : f.cover_letter_asked ? 'Yes' : 'No', false],
  ]
  return (
    <>
      <dl className="facts">
        {rows.map(([label, value, direct]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>
              {value && value !== 'unknown' ? value : <span className="muted">Not stated</span>}
              {direct && <span className="hint"> (from the board)</span>}
            </dd>
          </div>
        ))}
      </dl>
      <h3>Skills</h3>
      {f.skills?.length ? (
        <ul className="chips">
          {f.skills.map((s) => (
            <li key={s} className="chip">
              {s}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">None found.</p>
      )}
    </>
  )
}

export default function JobPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const [job, setJob] = useState<PostingDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    setJob(null)
    api.posting(id).then(setJob, (err) => setError(String(err)))
  }, [id])
  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  // While extraction is still running for this job, check again.
  useEffect(() => {
    if (!job || job.extracted) return
    const timer = setTimeout(() => void api.posting(id).then(setJob, () => undefined), 5000)
    return () => clearTimeout(timer)
  }, [job, id])

  return (
    <div className="panel-backdrop" onClick={onClose}>
      <aside className="panel" role="dialog" aria-modal="true" aria-labelledby="panel-title" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head">
          <h2 id="panel-title">{job?.title ?? 'Loading…'}</h2>
          <button ref={closeRef} type="button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        {error && <p className="error">{error}</p>}
        {job && (
          <div className="panel-body">
            <p className="lead">
              {job.company} · <span className={`ats ats-${job.source}`}>{atsLabel(job.source)}</span>
              {job.state === 'closed' && <span className="badge">Closed {shortDate(job.closed_at)}</span>}
            </p>
            <p className="muted">
              {job.locations.join(' · ') || 'No location given'}
              {job.department && ` · ${job.department}`}
              {job.employment_type && ` · ${job.employment_type}`} · Posted {shortDate(job.posted_at)}
            </p>
            <p>
              <a href={job.posting_url} target="_blank" rel="noreferrer">
                Open the posting on {atsLabel(job.source)}
              </a>
            </p>
            <section>
              <h3>What JAP read from it</h3>
              {job.fields && job.extracted ? (
                <Fields f={job.fields} />
              ) : (
                <p className="muted">Still reading this description…</p>
              )}
            </section>
            <section>
              <h3>Description</h3>
              <div className="jd">{job.description_text}</div>
            </section>
          </div>
        )}
      </aside>
    </div>
  )
}
