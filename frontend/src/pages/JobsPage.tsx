import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { go } from '../nav'
import {
  api,
  ApiError,
  OTHER_ROLE,
  type Condition,
  type Duplicate,
  type ListingFilters,
  type ListingPage,
  type ListingRow,
  type ListingStatus,
  type Run,
  type SortKey,
  type WorkMode,
} from '../api/client'
import JobDrawer from '../components/JobDrawer'
import { ago, atsLabel, errorText, fitsLine, plural, shortDate, STARTED_BY, STATUS, workModeLabel } from '../format'
import { useApp } from '../appContext'

type Strip = 'active' | 'not_applied' | 'needs_input' | 'ready_for_review' | 'applied' | 'failed'
const STRIP: { key: Strip; label: string; color: string }[] = [
  { key: 'active', label: 'All open', color: '' },
  { key: 'not_applied', label: 'New', color: 'var(--new-fg)' },
  { key: 'needs_input', label: 'Needs you', color: 'var(--you-fg)' },
  { key: 'ready_for_review', label: 'Ready to send', color: 'var(--ready-fg)' },
  { key: 'applied', label: 'Sent', color: 'var(--sent-fg)' },
  { key: 'failed', label: 'Failed', color: 'var(--fail-fg)' },
]
const COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'title', label: 'Job title' },
  { key: 'company', label: 'Company' },
  { key: 'location', label: 'Location' },
  { key: 'work_mode', label: 'Work mode' },
  { key: 'status', label: 'Status' },
  { key: 'posted', label: 'Posted' },
]
const FITS: { key: Condition; label: string }[] = [
  { key: 'role', label: 'Job title' },
  { key: 'location', label: 'Location' },
  { key: 'skills', label: 'Skills' },
]
const PER_PAGE = [10, 25, 50]
// Rows that can be ticked: not Sent, not Closed and not being worked on right now.
const pickable = (r: ListingRow) => !['applied', 'closed', 'applying'].includes(r.status)

type Filters = { q: string; role: string; location: string; work_mode: WorkMode | 'unknown' | ''; posted_days: string; matched: Condition | '' }
const NO_FILTERS: Filters = { q: '', role: '', location: '', work_mode: '', posted_days: '', matched: '' }

// Per-browser conveniences: rows per page, and which jobs I have opened (the unseen dot).
const store = {
  get: (k: string) => {
    try {
      return window.localStorage.getItem(k)
    } catch {
      return null
    }
  },
  set: (k: string, v: string) => {
    try {
      window.localStorage.setItem(k, v)
    } catch {
      /* private window: nothing kept */
    }
  },
}
function useSeen() {
  const [since] = useState(() => {
    const saved = store.get('jap.seenSince')
    if (saved) return saved
    const now = new Date().toISOString()
    store.set('jap.seenSince', now)
    return now
  })
  const [seen, setSeen] = useState<Set<string>>(() => new Set(JSON.parse(store.get('jap.seen') ?? '[]') as string[]))
  const mark = useCallback((id: string) => {
    setSeen((s) => {
      if (s.has(id)) return s
      const next = new Set(s).add(id)
      store.set('jap.seen', JSON.stringify([...next].slice(-2000)))
      return next
    })
  }, [])
  const unseen = (r: ListingRow) => r.status === 'not_applied' && r.found_at > since && !seen.has(r.id)
  return { unseen, mark }
}

function runCounts(run: Run, boldNew = false): ReactNode {
  const c = run.counts
  const fresh = `${c.new_listings ?? c.new ?? 0} new`
  const parts = [plural(c.boards ?? 0, 'company', 'companies'), `${plural(c.fetched ?? 0, 'job')} found`]
  const after: string[] = []
  if (c.merged) after.push(`${c.merged} merged as ${c.merged === 1 ? 'a duplicate' : 'duplicates'}`)
  if (run.mode === 'find_and_apply') after.push(`${c.apply_queued ?? 0} to apply`)
  return (
    <>
      {parts.join(', ')}, {boldNew ? <b>{fresh}</b> : fresh}
      {after.map((a) => `, ${a}`).join('')}
    </>
  )
}

/** When Jobs was last left: a search that ended after it stays green until Jobs is opened again. */
function useLeftAt() {
  const [leftAt] = useState(() => store.get('jap.jobsLeftAt') ?? '')
  useEffect(() => {
    const leave = () => store.set('jap.jobsLeftAt', new Date().toISOString())
    window.addEventListener('pagehide', leave)
    return () => {
      window.removeEventListener('pagehide', leave)
      leave()
    }
  }, [])
  return leftAt
}

const MIN_SEARCHING_MS = 3000 // a quick search still shows as searching long enough to be seen

/** The last search line: a pill coloured by the search's state (PRD 1.6, U2). */
function LastSearch({ onShowNew }: { onShowNew: () => void }) {
  const { runs, refresh, toast } = useApp()
  const leftAt = useLeftAt()
  const last = runs?.last
  const going = runs?.active ?? runs?.queued
  const [holdUntil, setHoldUntil] = useState(0)
  const [, rerender] = useState(0)
  useEffect(() => {
    if (going) setHoldUntil((h) => (h > Date.now() ? h : Date.now() + MIN_SEARCHING_MS))
  }, [going])
  useEffect(() => {
    const wait = holdUntil - Date.now()
    if (going || wait <= 0) return
    const t = window.setTimeout(() => rerender((n) => n + 1), wait)
    return () => window.clearTimeout(t)
  }, [going, holdUntil])
  const shown = going ?? (holdUntil > Date.now() ? last : undefined)
  const reading = (shown ?? last)?.extracting ?? 0
  const fresh = (last?.counts.new_listings ?? last?.counts.new ?? 0) > 0
  const tone = shown
    ? 'busy'
    : !last
      ? 'idle'
      : last.status === 'failed'
        ? 'bad'
        : last.counts.boards_failed || last.counts.spend_cap_reached
          ? 'warn'
          : fresh && (last.ended_at ?? last.created_at) > leftAt
            ? 'new'
            : 'idle'
  const retry = async () => {
    try {
      await api.startRun({ mode: last?.mode ?? 'find_only', roles: last?.scope.roles?.length ? last.scope.roles : undefined })
      await refresh()
    } catch (err) {
      toast({ text: errorText(err), bad: true })
    }
  }
  return (
    <div className="lastrun" role="status" data-testid="last-run">
      <span className={`runpill ${tone}`} data-tone={tone}>
        {tone === 'busy' && <i className="dot" aria-hidden="true" />}
        {shown ? (
          <span>
            {runs?.queued && !runs.active ? 'Waiting to search' : 'Searching'} {shown.scope.roles?.length ? `for ${shown.scope.roles.join(', ')}` : 'every company you follow'}…
          </span>
        ) : last ? (
          last.status === 'failed' ? (
            <span>
              Last search {ago(last.ended_at ?? last.created_at)} failed: {last.error ?? 'unknown error'}
            </span>
          ) : (
            <span>
              Last search {ago(last.ended_at ?? last.created_at)} ({STARTED_BY[last.trigger].toLowerCase() === 'you' ? 'by you' : STARTED_BY[last.trigger].toLowerCase()}
              {last.scope.roles?.length ? `, ${last.scope.roles.join(', ')} only` : ''}): {runCounts(last, tone === 'new')}
            </span>
          )
        ) : runs ? (
          <span>No search yet. Press Find new jobs.</span>
        ) : (
          <span>Loading…</span>
        )}
        {reading > 0 && <span>· Reading {plural(reading, 'job description')}…</span>}
        {!shown && !!last?.counts.boards_failed && (
          <span title={last.counts.board_errors?.join('\n')}>· {plural(last.counts.boards_failed, 'company', 'companies')} could not be read</span>
        )}
        {!shown && last?.counts.spend_cap_reached && <span>· AI limit reached</span>}
        {tone === 'new' && (
          <button type="button" className="btn-link" onClick={onShowNew}>
            Show new
          </button>
        )}
        {tone === 'bad' && (
          <button type="button" className="btn-link" onClick={() => void retry()}>
            Try again
          </button>
        )}
      </span>
      <button type="button" className="btn-link" onClick={() => go('settings')}>
        Search history
      </button>
    </div>
  )
}

function FindButton() {
  const { profile, runs, refresh, toast } = useApp()
  const [open, setOpen] = useState(false)
  const [picked, setPicked] = useState<string[]>([])
  const [andApply, setAndApply] = useState(false)
  const [starting, setStarting] = useState(false)
  const box = useRef<HTMLSpanElement>(null)
  const roles = profile?.target_roles ?? []
  const busy = !!runs?.active || !!runs?.queued
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false)
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDoc)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])
  const start = async (some?: string[]) => {
    setStarting(true)
    setOpen(false)
    try {
      await api.startRun({ mode: andApply ? 'find_and_apply' : 'find_only', roles: some?.length ? some : undefined })
      await refresh()
      toast(busy ? 'This search starts when the one going now ends.' : some?.length ? `Searching for ${some.join(', ')} only` : 'Searching every company you follow')
    } catch (err) {
      toast({ text: errorText(err), bad: true })
    } finally {
      setStarting(false)
    }
  }
  const automatic = profile?.apply_mode === 'automatic'
  return (
    <span className="split" ref={box}>
      <button type="button" className="btn-primary" disabled={starting || !!runs?.active} onClick={() => void start()}>
        {runs?.active ? 'Searching…' : 'Find new jobs'}
      </button>
      <button type="button" className="btn-primary split-arrow" aria-haspopup="dialog" aria-expanded={open} aria-label="More ways to find new jobs" onClick={() => setOpen(!open)}>
        ▾
      </button>
      {open && (
        <div className="menu" role="dialog" aria-label="Find new jobs for">
          <p className="label-sm">Only for these job titles</p>
          {roles.length === 0 && <p className="help">Add job titles in Job preferences first.</p>}
          {roles.map((r) => (
            <label key={r}>
              <input type="checkbox" checked={picked.includes(r)} onChange={() => setPicked(picked.includes(r) ? picked.filter((x) => x !== r) : [...picked, r])} /> {r}
            </label>
          ))}
          <hr />
          <label>
            <input type="checkbox" checked={andApply} onChange={(e) => setAndApply(e.target.checked)} />
            <span>
              Also apply to the new jobs that fit
              <span className="help" style={{ display: 'block' }}>
                {automatic ? `JAP sends each one with nothing unclear, up to ${profile?.daily_cap ?? 20} a day.` : 'JAP prepares each one; you send them from Ready to send.'}
              </span>
            </span>
          </label>
          <button type="button" className="btn-primary" disabled={starting} onClick={() => void start(picked)}>
            {picked.length ? `Find for ${plural(picked.length, 'job title')}` : 'Find for all job titles'}
          </button>
        </div>
      )}
    </span>
  )
}

function DuplicatesLine({ pairs, onDecided }: { pairs: Duplicate[]; onDecided: () => void }) {
  const { toast } = useApp()
  const d = pairs[0]
  if (!d) return null
  const decide = async (decision: 'merged' | 'kept_apart') => {
    try {
      await api.decide(d.id, decision)
      toast(decision === 'merged' ? 'Merged into one job.' : 'Kept as two jobs. JAP remembers this.')
      onDecided()
    } catch (err) {
      toast({ text: errorText(err), bad: true })
    }
  }
  const same = d.a.title === d.b.title
  return (
    <div className="dupes">
      <span>
        {pairs.length > 1 ? `${pairs.length} possible duplicates. First: ` : '1 possible duplicate: '}
        {same ? (
          <>
            <b>{d.a.title}</b> at {d.company} is listed twice ({[...d.a.platforms, ...d.b.platforms].map(atsLabel).join(' and ')}).
          </>
        ) : (
          <>
            <b>{d.a.title}</b> and <b>{d.b.title}</b> at {d.company} look like the same job.
          </>
        )}
      </span>
      <span className="inline">
        <button type="button" onClick={() => void decide('merged')}>
          Merge
        </button>
        <button type="button" onClick={() => void decide('kept_apart')}>
          Keep both
        </button>
      </span>
    </div>
  )
}

function ApplyModal({ rows, onCancel, onDone }: { rows: ListingRow[]; onCancel: () => void; onDone: () => void }) {
  const { profile, resume, refresh, toast } = useApp()
  const [busy, setBusy] = useState(false)
  const ok = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    ok.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onCancel()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])
  const ready = rows.filter((r) => r.status === 'not_applied')
  const other = rows.length - ready.length
  const n = ready.length
  const automatic = profile?.apply_mode === 'automatic'
  const unclear = ready.filter((r) => !r.work_mode).length
  const confirm = async () => {
    setBusy(true)
    let started = 0
    const problems: string[] = []
    for (const r of ready) {
      try {
        await api.applyNow(r.id)
        started++
      } catch (err) {
        problems.push(err instanceof ApiError && err.code === 'submit_maybe_sent' ? `${r.title}: check whether the last try was sent, then use Try again` : `${r.title}: ${errorText(err)}`)
      }
    }
    await refresh()
    setBusy(false)
    if (problems.length) toast({ text: `${started ? `${plural(started, 'application')} started. ` : ''}${problems.join('. ')}.`, bad: started === 0 })
    else toast(automatic ? `Applying to ${plural(started, 'job')}. Jobs with questions will wait for you.` : `${plural(started, 'application')} being prepared. Check ${started === 1 ? 'it' : 'them'} under Ready to send.`)
    onDone()
  }
  return (
    <div className="modal-bg" onClick={onCancel}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="apply-title" onClick={(e) => e.stopPropagation()}>
        <div>
          <h2 id="apply-title">Apply to {plural(n, 'job')}</h2>
          <ul>
            {automatic ? (
              <li>JAP fills in each form and sends it, up to {profile?.daily_cap ?? 20} a day. Forms with questions only you can answer wait for you under Needs you.</li>
            ) : (
              <li>
                JAP fills in each form. Nothing is sent: you check and send each one from <b>Ready to send</b>.
              </li>
            )}
            {automatic && unclear > 0 && <li>{plural(unclear, 'job')} with no work mode stated will wait for you under Ready to send.</li>}
            {resume ? (
              <li>
                Your resume <b>{resume.original_name}</b> is attached.
              </li>
            ) : (
              <li className="error-text">There is no resume yet. Upload one on Profile first.</li>
            )}
            {profile?.spend.reached && <li className="warn-text">The monthly AI limit is reached, so forms that need a written answer or a cover letter will stop for you.</li>}
            {other > 0 && <li className="muted">{plural(other, 'ticked job')} already {other === 1 ? 'has' : 'have'} an application and {other === 1 ? 'is' : 'are'} left out.</li>}
          </ul>
        </div>
        <div className="mfoot">
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
          <button ref={ok} type="button" className="btn-primary" disabled={busy || n === 0 || !resume} onClick={() => void confirm()}>
            {busy ? 'Starting…' : automatic ? `Apply to ${n}` : `Prepare ${n}`}
          </button>
        </div>
      </div>
    </div>
  )
}

function pageList(page: number, pages: number): (number | '…')[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1)
  const out: (number | '…')[] = [1]
  for (let i = Math.max(2, page - 1); i <= Math.min(pages - 1, page + 1); i++) out.push(i)
  if ((out[1] as number) > 2) out.splice(1, 0, '…')
  if ((out[out.length - 1] as number) < pages - 1) out.push('…')
  out.push(pages)
  return out
}

export default function JobsPage() {
  const { profile, summary, tick, refresh, toast } = useApp()
  const [status, setStatus] = useState<Strip>('active')
  const [showHidden, setShowHidden] = useState(false)
  const [filters, setFilters] = useState<Filters>(NO_FILTERS)
  const [q, setQ] = useState('') // the search box; filters.q follows it after a pause
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'posted', dir: 'desc' })
  const [pageNo, setPageNo] = useState(1)
  const [per, setPer] = useState(() => {
    const n = Number(store.get('jap.perPage'))
    return PER_PAGE.includes(n) ? n : 25
  })
  const [page, setPage] = useState<ListingPage | null>(null)
  const [pairs, setPairs] = useState<Duplicate[]>([])
  const [picked, setPicked] = useState<Map<string, ListingRow>>(new Map())
  const [applying, setApplying] = useState<ListingRow[] | null>(null)
  // ?listing=<id> opens that job (the confirmation and alert emails link here).
  const [openId, setOpenId] = useState<string | null>(() => new URLSearchParams(window.location.search).get('listing'))
  const [error, setError] = useState<string | null>(null)
  const { unseen, mark } = useSeen()

  useEffect(() => {
    const t = setTimeout(() => {
      setFilters((f) => (f.q === q ? f : { ...f, q }))
      setPageNo(1)
    }, 250)
    return () => clearTimeout(t)
  }, [q])

  const query: ListingFilters = useMemo(
    () => ({
      status: showHidden ? 'skipped' : (status as ListingFilters['status']),
      q: filters.q.trim() || undefined,
      role: filters.role || undefined,
      location: filters.location || undefined,
      work_mode: filters.work_mode || undefined,
      posted_days: filters.posted_days ? Number(filters.posted_days) : undefined,
      matched: filters.matched || undefined,
      sort: sort.key,
      dir: sort.dir,
      page: pageNo,
      page_size: per,
    }),
    [status, showHidden, filters, sort, pageNo, per],
  )
  useEffect(() => {
    let live = true
    Promise.all([api.listings(query), api.duplicates()]).then(
      ([p, d]) => {
        if (!live) return
        setPage(p)
        setPairs(d)
        setError(null)
        const pages = Math.max(1, Math.ceil(p.total / p.page_size))
        if (pageNo > pages) setPageNo(pages)
      },
      (err) => live && setError(errorText(err)),
    )
    return () => {
      live = false
    }
  }, [query, tick, pageNo])

  const t = summary?.totals
  const count: Record<Strip, number> = {
    active: t ? t.listings - t.applied - t.skipped : 0,
    not_applied: t?.not_applied ?? 0,
    needs_input: t?.needs_input ?? 0,
    ready_for_review: t?.ready_for_review ?? 0,
    applied: t?.applied ?? 0,
    failed: t?.failed ?? 0,
  }
  const set = <K extends keyof Filters>(key: K, value: Filters[K]) => {
    setFilters({ ...filters, [key]: value })
    setPageNo(1)
  }
  const filtered = JSON.stringify({ ...filters, q }) !== JSON.stringify(NO_FILTERS)
  const clear = () => {
    setFilters(NO_FILTERS)
    setQ('')
    setPageNo(1)
  }
  const places = useMemo(() => {
    const seen = new Map<string, string>()
    for (const l of summary?.locations ?? []) {
      const name = /remote/i.test(l) ? 'Remote' : l.split(/,|\s-\s/)[0].trim()
      if (name && !seen.has(name.toLowerCase())) seen.set(name.toLowerCase(), name)
    }
    return [...seen.values()].sort((a, b) => (a === 'Remote' ? -1 : b === 'Remote' ? 1 : a.localeCompare(b)))
  }, [summary?.locations])

  const rows = page?.rows ?? []
  const pages = page ? Math.max(1, Math.ceil(page.total / page.page_size)) : 1
  const start = page && page.total ? (page.page - 1) * page.page_size + 1 : 0
  const end = page ? Math.min(page.page * page.page_size, page.total) : 0
  const tickable = rows.filter(pickable)
  const allTicked = tickable.length > 0 && tickable.every((r) => picked.has(r.id))
  const tickRow = (r: ListingRow, on: boolean) => {
    const next = new Map(picked)
    if (on) next.set(r.id, r)
    else next.delete(r.id)
    setPicked(next)
  }
  const tickPage = (on: boolean) => {
    const next = new Map(picked)
    for (const r of tickable) {
      if (on) next.set(r.id, r)
      else next.delete(r.id)
    }
    setPicked(next)
  }
  const hideTicked = async () => {
    const ids = [...picked.keys()]
    const done: string[] = []
    for (const id of ids) {
      try {
        await api.skip(id)
        done.push(id)
      } catch {
        /* reported below */
      }
    }
    setPicked(new Map())
    await refresh()
    toast({
      text: `${plural(done.length, 'job')} hidden${done.length < ids.length ? `; ${ids.length - done.length} could not be (JAP is working on them)` : ''}.`,
      undo: () => void Promise.all(done.map((id) => api.unskip(id))).then(() => refresh()),
    })
  }
  const sortBy = (key: SortKey) => {
    setSort(sort.key === key ? { key, dir: sort.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: key === 'posted' ? 'desc' : 'asc' })
    setPageNo(1)
  }
  const open = (id: string) => {
    mark(id)
    setOpenId(id)
  }
  const close = useCallback(() => {
    setOpenId(null)
    if (window.location.search) window.history.replaceState(null, '', window.location.pathname + window.location.hash)
  }, [])
  const roles = summary?.by_role ?? []
  const spend = profile?.spend

  return (
    <section className="page">
      <div className="page-head">
        <div>
          <h1>Jobs</h1>
          <p>Jobs from the companies you follow that fit your job preferences.</p>
          <LastSearch
            onShowNew={() => {
              setStatus('not_applied')
              setShowHidden(false)
              setPageNo(1)
            }}
          />
        </div>
        <FindButton />
      </div>

      {spend?.reached && (
        <div className="banner" role="status">
          <span>
            <b>AI limit reached</b> ({`$${spend.month_usd.toFixed(2)}`} this month). JAP has stopped reading job descriptions and writing answers and cover letters, and won&apos;t send anything that needs them.
          </span>
          <button type="button" onClick={() => go('settings')}>
            Raise the limit
          </button>
        </div>
      )}

      <div className="strip" role="group" aria-label="Show jobs by status">
        {STRIP.map((s) => (
          <button
            key={s.key}
            type="button"
            aria-pressed={!showHidden && status === s.key}
            className={s.key === 'needs_input' && count[s.key] > 0 ? 'attn' : undefined}
            onClick={() => {
              setStatus(s.key)
              setShowHidden(false)
              setPageNo(1)
            }}
          >
            <span className="n">{summary ? count[s.key] : '–'}</span>
            <span className="l">
              {s.color && <i style={{ background: s.color }} />}
              {s.label}
            </span>
          </button>
        ))}
      </div>

      <div className="tcard">
        {picked.size > 0 ? (
          <div className="toolbar bulk">
            <span>
              <b>{picked.size} selected</b>
            </span>
            <button type="button" className="btn-primary" onClick={() => setApplying([...picked.values()])}>
              Apply to {plural(picked.size, 'job')}
            </button>
            <button type="button" onClick={() => void hideTicked()}>
              Hide
            </button>
            <button type="button" className="btn-quiet" onClick={() => setPicked(new Map())}>
              Clear selection
            </button>
          </div>
        ) : (
          <div className="toolbar" role="search">
            <div className="search">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <circle cx="11" cy="11" r="7" />
                <path d="m20 20-3.5-3.5" />
              </svg>
              <input id="job-search" type="search" placeholder="Search title, company or location" title="Tip: press / anywhere to jump here" aria-label="Search jobs" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            <select aria-label="Job title" value={filters.role} onChange={(e) => set('role', e.target.value)}>
              <option value="">All job titles</option>
              {roles.map((r) => (
                <option key={r.role ?? OTHER_ROLE} value={r.role ?? OTHER_ROLE}>
                  {r.role ?? 'Other (fits on skills or location)'} ({r.listings})
                </option>
              ))}
            </select>
            <select aria-label="Location" value={filters.location} onChange={(e) => set('location', e.target.value)}>
              <option value="">All locations</option>
              {places.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
            <select aria-label="Work mode" value={filters.work_mode} onChange={(e) => set('work_mode', e.target.value as Filters['work_mode'])}>
              <option value="">Any work mode</option>
              <option value="remote">Remote</option>
              <option value="hybrid">Hybrid</option>
              <option value="onsite">On-site</option>
              <option value="unknown">Not stated</option>
            </select>
            <select aria-label="Posted" value={filters.posted_days} onChange={(e) => set('posted_days', e.target.value)}>
              <option value="">Posted any time</option>
              <option value="1">Last 24 hours</option>
              <option value="7">Last 7 days</option>
              <option value="30">Last 30 days</option>
            </select>
            <select aria-label="Fits on" value={filters.matched} onChange={(e) => set('matched', e.target.value as Filters['matched'])}>
              <option value="">Fits on anything</option>
              {FITS.map((f) => (
                <option key={f.key} value={f.key}>
                  Fits on {f.label.toLowerCase()}
                </option>
              ))}
            </select>
            {filtered && (
              <button type="button" className="btn-quiet" style={{ color: 'var(--accent)' }} onClick={clear}>
                Clear filters
              </button>
            )}
          </div>
        )}
        {showHidden && (
          <div className="hidden-head">
            <span>
              Showing <b>hidden and skipped jobs</b>. Open one to undo.
            </span>
            <button type="button" className="btn-link" onClick={() => setShowHidden(false)}>
              Back to all jobs
            </button>
          </div>
        )}
        {!showHidden && <DuplicatesLine pairs={pairs} onDecided={() => void refresh()} />}
        {error && (
          <p className="note-box bad" style={{ margin: 12 }} role="alert">
            {error}
          </p>
        )}
        <div className="table-scroll">
          <table className="jobs">
            <colgroup>
              <col style={{ width: 44 }} />
              <col style={{ width: '30%' }} />
              <col style={{ width: '17%' }} />
              <col style={{ width: '17%' }} />
              <col style={{ width: '11%' }} />
              <col style={{ width: '14%' }} />
              <col style={{ width: '11%' }} />
            </colgroup>
            <thead>
              <tr>
                <th className="c-sel">
                  <input type="checkbox" aria-label="Select all on this page" checked={allTicked} disabled={tickable.length === 0} onChange={(e) => tickPage(e.target.checked)} />
                </th>
                {COLUMNS.map((c) => (
                  <th key={c.key} className={c.key === 'title' ? 'c-title' : undefined} aria-sort={sort.key === c.key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined}>
                    <button type="button" onClick={() => sortBy(c.key)}>
                      {c.label} <span className="arrow">{sort.key === c.key ? (sort.dir === 'asc' ? '▲' : '▼') : '▲▼'}</span>
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {page && rows.length === 0 ? (
                <tr>
                  <td colSpan={7} style={{ height: 'auto', cursor: 'default' }}>
                    <div className="empty">
                      {filtered ? (
                        <>
                          <b>No jobs match</b>
                          <span>Try a different search or clear the filters.</span>
                          <button type="button" onClick={clear}>
                            Clear filters
                          </button>
                        </>
                      ) : showHidden ? (
                        <b>No hidden or skipped jobs</b>
                      ) : status !== 'active' ? (
                        <b>No jobs here right now</b>
                      ) : (
                        <>
                          <b>No jobs yet</b>
                          <span>Add job titles and locations in Job preferences and companies to follow, then press Find new jobs.</span>
                          <button type="button" onClick={() => go('prefs')}>
                            Open Job preferences
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ) : (
                rows.map((r) => {
                  const [label, cls] = STATUS[r.status as ListingStatus]
                  const fits = fitsLine(r)
                  const loc = r.locations.join(' · ') || '–'
                  return (
                    <tr key={r.id} className={[picked.has(r.id) ? 'picked' : '', r.status === 'closed' ? 'closed' : ''].join(' ').trim() || undefined} onClick={() => open(r.id)}>
                      <td className="c-sel" onClick={(e) => e.stopPropagation()}>
                        <input type="checkbox" aria-label={`Select ${r.title} at ${r.company}`} checked={picked.has(r.id)} disabled={!pickable(r)} onChange={(e) => tickRow(r, e.target.checked)} />
                      </td>
                      <td className="c-title">
                        <div className="t-title" title={r.title}>
                          {unseen(r) && <span className="unseen" title="Not opened yet" />}
                          <button type="button" className="btn-link" style={{ color: 'inherit', fontWeight: 600 }} onClick={(e) => { e.stopPropagation(); open(r.id) }}>
                            {r.title}
                          </button>
                        </div>
                        {fits && (
                          <div className="t-sub" title={fits}>
                            {fits}
                            {r.nice_skills.length > 0 && ` · also ${r.nice_skills.slice(0, 2).join(', ')}`}
                          </div>
                        )}
                      </td>
                      <td title={r.company}>
                        <div style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.company}</div>
                        <div className="t-sub">
                          via {r.platforms.map(atsLabel).join(', ') || '–'}
                          {r.platforms.length > 1 && ` · ${r.platforms.length} boards`}
                        </div>
                      </td>
                      <td title={loc}>{loc}</td>
                      <td>{r.work_mode ? workModeLabel(r.work_mode) : <span className="mode-unclear">Not stated</span>}</td>
                      <td>
                        <span className={`pill ${cls}`}>{label}</span>
                      </td>
                      <td className="mono">{shortDate(r.last_posted_at)}</td>
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </div>
        <div className="foot">
          <button type="button" className="btn-link" onClick={() => { setShowHidden(!showHidden); setPageNo(1) }}>
            {showHidden ? 'Back to all jobs' : `Show hidden and skipped jobs${t?.skipped ? ` (${t.skipped})` : ''}`}
          </button>
          <label className="inline">
            Rows per page
            <select
              value={per}
              onChange={(e) => {
                setPer(Number(e.target.value))
                store.set('jap.perPage', e.target.value)
                setPageNo(1)
              }}
            >
              {PER_PAGE.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <span className="inline">
            <span className="mono">{page ? (page.total ? `${start}–${end} of ${page.total}` : '0 of 0') : ''}</span>
            <span className="pages">
              <button type="button" aria-label="Previous page" disabled={pageNo <= 1} onClick={() => setPageNo(pageNo - 1)}>
                ‹
              </button>
              {pageList(pageNo, pages).map((p, i) =>
                p === '…' ? (
                  <span key={`g${i}`} className="gap">
                    …
                  </span>
                ) : (
                  <button key={p} type="button" aria-current={p === pageNo ? 'page' : undefined} onClick={() => setPageNo(p)}>
                    {p}
                  </button>
                ),
              )}
              <button type="button" aria-label="Next page" disabled={pageNo >= pages} onClick={() => setPageNo(pageNo + 1)}>
                ›
              </button>
            </span>
          </span>
        </div>
      </div>

      {openId && (
        <JobDrawer
          id={openId}
          onClose={close}
          onOpen={open}
          onApply={(l) => {
            setOpenId(null)
            setApplying([l])
          }}
        />
      )}
      {applying && (
        <ApplyModal
          rows={applying}
          onCancel={() => setApplying(null)}
          onDone={() => {
            setApplying(null)
            setPicked(new Map())
          }}
        />
      )}
    </section>
  )
}
