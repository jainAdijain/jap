import { useCallback, useEffect, useRef, useState } from 'react'

import { api, type ListingDetail } from '../api/client'
import { atsLabel, shortDate } from '../format'
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
  // While the primary posting's description is still being read, check again.
  useEffect(() => {
    if (!listing?.primary || listing.primary.extracted) return
    const timer = setTimeout(() => void load(), 5000)
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
            {job && (
              <p>
                <a href={job.posting_url} target="_blank" rel="noreferrer">
                  Open the posting on {atsLabel(job.source)}
                </a>
              </p>
            )}

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
            {job && (
              <section>
                <h3>Description</h3>
                <div className="jd">{job.description_text}</div>
              </section>
            )}
          </div>
        )}
      </aside>
    </div>
  )
}
