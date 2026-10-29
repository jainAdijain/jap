import { useCallback, useEffect, useRef, useState } from 'react'

import {
  api,
  OTHER_ROLE,
  type Condition,
  type Duplicate,
  type ListingFilters,
  type ListingPage,
  type ListingSummary,
  type Run,
} from '../api/client'
import Duplicates from '../components/Duplicates'
import ListingPanel from '../components/ListingPanel'
import Platforms from '../components/Platforms'
import RunBar from '../components/RunBar'
import Summary from '../components/Summary'
import { CONDITION_LABELS, joinAnd, shortDate, STATUS_LABELS, workModeLabel } from '../format'

const POLL_MS = 5000

const busy = (run: Run | null | undefined) =>
  !!run && (run.status === 'queued' || run.status === 'running' || run.extracting > 0)

type Filters = Required<Omit<ListingFilters, 'page' | 'matched'>> & { matched: Condition | '' }
const NO_FILTERS: Filters = { q: '', status: 'open', role: '', location: '', matched: '' }

export default function DashboardPage() {
  const [run, setRun] = useState<Run | null | undefined>(undefined)
  const [page, setPage] = useState<ListingPage | null>(null)
  const [summary, setSummary] = useState<ListingSummary | null>(null)
  const [pairs, setPairs] = useState<Duplicate[]>([])
  const [pageNo, setPageNo] = useState(1)
  const [filters, setFilters] = useState<Filters>(NO_FILTERS)
  const [openId, setOpenId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const query = useRef({ filters, pageNo })
  query.current = { filters, pageNo }

  const refresh = useCallback(async () => {
    const { filters: f, pageNo } = query.current
    const [latest, listings, totals, dupes] = await Promise.all([
      api.latestRun(),
      api.listings({
        q: f.q.trim() || undefined,
        status: f.status,
        role: f.role || undefined,
        location: f.location.trim() || undefined,
        matched: f.matched || undefined,
        page: pageNo,
      }),
      api.summary(),
      api.duplicates(),
    ])
    setRun(latest)
    setPage(listings)
    setSummary(totals)
    setPairs(dupes)
    setError(null)
  }, [])

  useEffect(() => {
    refresh().catch((err) => setError(String(err)))
  }, [refresh, filters, pageNo])

  // New listings appear without a reload: poll while a run or its extraction is going (T2).
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
  const changed = () => void refresh().catch((err) => setError(String(err)))
  const set = <K extends keyof Filters>(key: K, value: Filters[K]) => {
    setFilters({ ...filters, [key]: value })
    setPageNo(1)
  }
  const filtered = JSON.stringify(filters) !== JSON.stringify(NO_FILTERS)

  const pages = page ? Math.max(1, Math.ceil(page.total / page.page_size)) : 1
  const roles = summary?.by_role.map((r) => r.role).filter((r): r is string => r !== null) ?? []

  return (
    <>
      <RunBar run={run} onRun={() => void start()} />
      <Summary summary={summary} onRole={(role) => set('role', role ?? OTHER_ROLE)} />
      <Duplicates pairs={pairs} onChanged={changed} />
      <section className="card">
        <div className="card-head">
          <h1>
            Jobs <span className="count">{page ? page.total : '…'}</span>
          </h1>
        </div>
        <p className="hint">
          Jobs that match your profile&apos;s match rule, one row per company and role, newest first. The
          filters below narrow this list without changing your profile.
        </p>
        <div className="filter-bar" role="search">
          <label>
            Role
            <select value={filters.role} onChange={(e) => set('role', e.target.value)}>
              <option value="">All roles</option>
              {roles.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
              <option value={OTHER_ROLE}>Other (skills or location)</option>
            </select>
          </label>
          <label>
            Location
            <input
              type="search"
              placeholder="e.g. Bangalore or Remote"
              value={filters.location}
              onChange={(e) => set('location', e.target.value)}
            />
          </label>
          <label>
            Status
            <select value={filters.status} onChange={(e) => set('status', e.target.value as Filters['status'])}>
              <option value="open">All open</option>
              {Object.entries(STATUS_LABELS).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
              <option value="all">Everything, closed too</option>
            </select>
          </label>
          <label>
            Matched on
            <select value={filters.matched} onChange={(e) => set('matched', e.target.value as Filters['matched'])}>
              <option value="">Any condition</option>
              {Object.entries(CONDITION_LABELS).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Search
            <input
              type="search"
              placeholder="Title or company"
              value={filters.q}
              onChange={(e) => set('q', e.target.value)}
            />
          </label>
          {filtered && (
            <button type="button" onClick={() => { setFilters(NO_FILTERS); setPageNo(1) }}>
              Clear filters
            </button>
          )}
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        {page && page.rows.length === 0 ? (
          <p className="muted">
            {filtered
              ? 'No jobs match these filters.'
              : 'No jobs match your profile yet. Add roles, skills or locations in My profile, or add companies and press Run.'}
          </p>
        ) : (
          <div className="table-wrap">
            <table className="jobs">
              <thead>
                <tr>
                  <th scope="col">Title</th>
                  <th scope="col">Company</th>
                  <th scope="col" className="col-locations">Locations</th>
                  <th scope="col" className="col-mode">Work mode</th>
                  <th scope="col">Status</th>
                  <th scope="col">Posted</th>
                </tr>
              </thead>
              <tbody>
                {page?.rows.map((r) => (
                  <tr key={r.id} className={r.status === 'closed' ? 'closed' : undefined}>
                    <td>
                      <button type="button" className="link" onClick={() => setOpenId(r.id)}>
                        {r.title}
                      </button>
                      <div className="row-tags">
                        <span className="matched" title="The conditions of your match rule this job meets">
                          Matched {joinAnd(r.matched.map((c) => CONDITION_LABELS[c]))}
                        </span>
                        {r.matched_role && <span className="role-tag">{r.matched_role}</span>}
                        {r.nice_skills.slice(0, 3).map((s) => (
                          <span key={s} className="nice" title="One of your nice-to-have skills">
                            {s}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td>
                      {r.company} <Platforms list={r.platforms} />
                    </td>
                    <td className="col-locations">{r.locations.join(' · ') || '–'}</td>
                    <td className="col-mode">
                      {r.work_mode ? workModeLabel(r.work_mode) : <span className="unclear">Work mode unclear</span>}
                    </td>
                    <td className="nowrap">{STATUS_LABELS[r.status]}</td>
                    <td className="nowrap">{shortDate(r.last_posted_at)}</td>
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
      {openId && <ListingPanel id={openId} onClose={() => setOpenId(null)} onChanged={changed} onOpen={setOpenId} />}
    </>
  )
}
