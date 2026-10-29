import type { Run } from '../api/client'

const TRIGGERS: Record<Run['trigger'], string> = {
  manual: 'Run button',
  scheduled: 'Daily run',
  resume_updated: 'Resume update',
}

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

function summary(run: Run): string {
  const c = run.counts
  if (run.status === 'queued') return 'Waiting to start…'
  if (run.status === 'running') return 'Fetching boards…'
  if (run.status === 'failed') return `Failed: ${run.error ?? 'unknown error'}`
  const parts = [
    `${c.boards ?? 0} board${c.boards === 1 ? '' : 's'}`,
    `${c.fetched ?? 0} jobs`,
    `${c.new ?? 0} new`,
  ]
  if (c.changed) parts.push(`${c.changed} changed`)
  if (c.closed) parts.push(`${c.closed} closed`)
  if (c.boards_failed) parts.push(`${c.boards_failed} board${c.boards_failed === 1 ? '' : 's'} failed`)
  return parts.join(' · ')
}

export default function RunBar({ run, onRun }: { run: Run | null | undefined; onRun: () => void }) {
  const active = !!run && (run.status === 'queued' || run.status === 'running')
  return (
    <section className="card runbar">
      <div className="card-head">
        <div>
          <h2>Last run</h2>
          {run ? (
            <p className={run.status === 'failed' ? 'error' : 'lead'} role="status">
              {when(run.created_at)}, from the {TRIGGERS[run.trigger]}: {summary(run)}
            </p>
          ) : (
            <p className="lead">{run === undefined ? 'Loading…' : 'No run yet.'}</p>
          )}
          {run && run.extracting > 0 && (
            <p className="hint" role="status">
              Reading {run.extracting} job description{run.extracting === 1 ? '' : 's'} for skills, salary and work mode…
            </p>
          )}
          {run?.counts.board_errors?.map((e) => (
            <p key={e} className="hint error">
              {e}
            </p>
          ))}
        </div>
        <button type="button" className="primary" disabled={active} onClick={onRun}>
          {active ? 'Running…' : 'Run: find only'}
        </button>
      </div>
    </section>
  )
}
