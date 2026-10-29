import { useCallback, useEffect, useRef, useState } from 'react'

import { api, type PostingPage, type Run } from '../api/client'
import JobPanel from '../components/JobPanel'
import RunBar from '../components/RunBar'
import { atsLabel, shortDate } from '../format'

const POLL_MS = 5000

const busy = (run: Run | null | undefined) =>
  !!run && (run.status === 'queued' || run.status === 'running' || run.extracting > 0)

export default function DashboardPage() {
  const [run, setRun] = useState<Run | null | undefined>(undefined)
  const [page, setPage] = useState<PostingPage | null>(null)
  const [pageNo, setPageNo] = useState(1)
  const [q, setQ] = useState('')
  const [showClosed, setShowClosed] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const query = useRef({ q, showClosed, pageNo })
  query.current = { q, showClosed, pageNo }

  const refresh = useCallback(async () => {
    const { q, showClosed, pageNo } = query.current
    const [latest, jobs] = await Promise.all([
      api.latestRun(),
      api.postings({ q: q.trim() || undefined, state: showClosed ? 'all' : 'open', page: pageNo }),
    ])
    setRun(latest)
    setPage(jobs)
    setError(null)
  }, [])

  useEffect(() => {
    refresh().catch((err) => setError(String(err)))
  }, [refresh, q, showClosed, pageNo])

  // New jobs appear without a reload: poll while a run or its extraction is going (T2).
  const polling = busy(run)
  useEffect(() => {
    if (!polling) return
    const timer = setInterval(() => void refresh().catch(() => undefined), POLL_MS)
    return () => clearInterval(timer)
  }, [polling, refresh])

  const start = async () => {
    try {
      setRun(await api.startRun())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const pages = page ? Math.max(1, Math.ceil(page.total / page.page_size)) : 1

  return (
    <>
      <RunBar run={run} onRun={() => void start()} />
      <section className="card">
        <div className="card-head">
          <h1>
            Jobs found <span className="count">{page ? page.total : '…'}</span>
          </h1>
          <div className="list-tools">
            <label className="sr-only" htmlFor="job-search">Search title or company</label>
            <input
              id="job-search"
              type="search"
              placeholder="Search title or company"
              value={q}
              onChange={(e) => {
                setQ(e.target.value)
                setPageNo(1)
              }}
            />
            <label className="check">
              <input
                type="checkbox"
                checked={showClosed}
                onChange={(e) => {
                  setShowClosed(e.target.checked)
                  setPageNo(1)
                }}
              />
              Show closed
            </label>
          </div>
        </div>
        <p className="hint">
          Every job from your Companies to watch, newest first. Matching to your profile and
          merging the same job across boards come in later phases.
        </p>
        {error && <p className="error" role="alert">{error}</p>}
        {page && page.rows.length === 0 ? (
          <p className="muted">
            {q ? 'No jobs match that search.' : 'No jobs yet. Add companies in My profile, then press Run.'}
          </p>
        ) : (
          <div className="table-wrap">
            <table className="jobs">
              <thead>
                <tr>
                  <th scope="col">Title</th>
                  <th scope="col">Company</th>
                  <th scope="col" className="col-locations">Locations</th>
                  <th scope="col">Posted</th>
                </tr>
              </thead>
              <tbody>
                {page?.rows.map((r) => (
                  <tr key={r.id} className={r.state === 'closed' ? 'closed' : undefined}>
                    <td>
                      <button type="button" className="link" onClick={() => setOpenId(r.id)}>
                        {r.title}
                      </button>
                      {r.state === 'closed' && <span className="badge">Closed</span>}
                    </td>
                    <td>
                      {r.company} <span className={`ats ats-${r.source}`}>{atsLabel(r.source)}</span>
                    </td>
                    <td className="col-locations">{r.locations.join(' · ') || '–'}</td>
                    <td className="nowrap">{shortDate(r.posted_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {pages > 1 && (
          <div className="pager">
            <button type="button" disabled={pageNo <= 1} onClick={() => setPageNo(pageNo - 1)}>
              Previous
            </button>
            <span className="muted">
              Page {pageNo} of {pages}
            </span>
            <button type="button" disabled={pageNo >= pages} onClick={() => setPageNo(pageNo + 1)}>
              Next
            </button>
          </div>
        )}
      </section>
      {openId && <JobPanel id={openId} onClose={() => setOpenId(null)} />}
    </>
  )
}
