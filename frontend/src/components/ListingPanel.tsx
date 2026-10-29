import { useCallback, useEffect, useRef, useState } from 'react'

import { api, type ListingDetail } from '../api/client'
import { atsLabel, CONDITION_LABELS, joinAnd, shortDate, STATUS_LABELS, workModeLabel } from '../format'
import ApplyPanel from './ApplyPanel'
import JobFields from './JobFields'
import Platforms from './Platforms'

type Props = { id: string; onClose: () => void; onChanged: () => void; onOpen: (id: string) => void }

export default function ListingPanel({ id, onClose, onChanged, onOpen }: Props) {
  const [listing, setListing] = useState<ListingDetail | null>(null)
  const [mergeWith, setMergeWith] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const closeRef = useRef<HTMLButtonElement>(null)

  const load = useCallback(() => api.listing(id).then(setListing, (err) => setError(String(err))), [id])
  useEffect(() => {
    setListing(null)
    setMergeWith('')
    setError(null)
    void load()
  }, [load])
  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  // While the primary posting's description is still being read, or JAP is filling or
  // submitting the form, check again.
  useEffect(() => {
    if (!listing) return
    const working = listing.status === 'applying' || listing.application?.status === 'preparing'
    if (!working && (!listing.primary || listing.primary.extracted)) return
    const timer = setTimeout(() => void load(), working ? 2000 : 5000)
    return () => clearTimeout(timer)
  }, [listing, load])

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const job = listing?.primary
  // U12: the frozen snapshot once an attempt has started, else the latest fetched copy.
  const frozen = listing?.application?.status === 'applied' ? listing.application.jd : null
  return (
    <div className="panel-backdrop" onClick={onClose}>
      <aside className="panel" role="dialog" aria-modal="true" aria-labelledby="panel-title" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head">
          <h2 id="panel-title">{listing?.title ?? 'Loading…'}</h2>
          <button ref={closeRef} type="button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        {listing && (
          <div className="panel-body">
            <p className="lead">
              {listing.company} <Platforms list={listing.platforms} />
              {listing.status === 'closed' && <span className="badge">Closed</span>}
            </p>
            <p className="muted">
              {listing.locations.join(' · ') || 'No location given'}
              {job?.department && ` · ${job.department}`}
              {job?.employment_type && ` · ${job.employment_type}`} · Posted {shortDate(listing.last_posted_at)}
            </p>
            <p className="muted">
              {workModeLabel(listing.work_mode)} · {STATUS_LABELS[listing.status]}
            </p>
            <div className="row-tags">
              {listing.matched.length > 0 ? (
                <span className="matched">Matched {joinAnd(listing.matched.map((c) => CONDITION_LABELS[c]))}</span>
              ) : (
                <span className="muted">Doesn&apos;t match your profile</span>
              )}
              {listing.matched_role && <span className="role-tag">{listing.matched_role}</span>}
              {listing.nice_skills.map((s) => (
                <span key={s} className="nice" title="One of your nice-to-have skills">
                  {s}
                </span>
              ))}
            </div>
            {job && (
              <p>
                <a href={job.posting_url} target="_blank" rel="noreferrer">
                  Open the posting on {atsLabel(job.source)}
                </a>
              </p>
            )}

            <ApplyPanel
              listing={listing}
              onChanged={(detail) => {
                if (detail) setListing(detail)
                else void load()
                onChanged()
              }}
            />

            <section>
              <h3>Also on</h3>
              {listing.also_on.length === 0 ? (
                <p className="muted">Only on this board.</p>
              ) : (
                <ul className="also-on">
                  {listing.also_on.map((a) => (
                    <li key={a.posting_id}>
                      <div>
                        <a href={a.posting_url} target="_blank" rel="noreferrer">
                          {atsLabel(a.source)}: {a.title}
                        </a>
                        {a.state === 'closed' && <span className="badge">Closed</span>}
                        <div className="hint">{a.locations.join(' · ') || 'No location given'}</div>
                      </div>
                      <button
                        type="button"
                        disabled={busy}
                        title="Make this posting a listing of its own; remembered on later runs"
                        onClick={() => void act(async () => {
                          const r = await api.split(a.posting_id)
                          onOpen(r.listing_id)
                        })}
                      >
                        Split
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {listing.same_company.length > 0 && (
                <div className="add-row merge-row">
                  <label className="sr-only" htmlFor="merge-with">Merge another {listing.company} listing into this one</label>
                  <select id="merge-with" value={mergeWith} onChange={(e) => setMergeWith(e.target.value)}>
                    <option value="">Merge another {listing.company} listing into this one…</option>
                    {listing.same_company.map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.title}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    disabled={!mergeWith || busy}
                    onClick={() => void act(async () => {
                      setListing(await api.merge(listing.id, mergeWith))
                      setMergeWith('')
                    })}
                  >
                    Merge
                  </button>
                </div>
              )}
            </section>

            <section>
              <h3>What JAP read from it</h3>
              {job?.fields && job.extracted ? <JobFields f={job.fields} /> : <p className="muted">Still reading this description…</p>}
            </section>
            {frozen ? (
              <section>
                <h3>Description</h3>
                <p className="hint">As it was when you applied: captured {frozen.captured_at ? new Date(frozen.captured_at).toLocaleString() : '–'}.</p>
                <div className="jd">{frozen.text}</div>
              </section>
            ) : (
              job && (
                <section>
                  <h3>Description</h3>
                  <div className="jd">{job.description_text}</div>
                </section>
              )
            )}
          </div>
        )}
      </aside>
    </div>
  )
}
