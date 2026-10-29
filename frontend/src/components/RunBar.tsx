import { useState } from 'react'

import type { Profile, Run, RunRequest, RunState, Switches } from '../api/client'
import RunDialog from './RunDialog'

const TRIGGERS: Record<Run['trigger'], string> = {
  manual: 'Run button',
  scheduled: 'Daily run',
  resume_updated: 'resume update',
  profile_saved: 'profile change',
}

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

const hhmm = (t: string) => t.slice(0, 5)

function scopeText(run: Run): string {
  const roles = run.scope.roles ?? []
  return roles.length ? `, ${roles.join(', ')} only` : ''
}

const n = (count: number | undefined, word: string) => `${count ?? 0} ${word}${count === 1 ? '' : 's'}`

function counts(run: Run): string {
  const c = run.counts
  const parts = [n(c.boards, 'board'), `${n(c.fetched, 'job')} fetched`, `${c.new ?? 0} new`, `${c.merged ?? 0} merged as duplicates`]
  if (c.listed !== undefined) parts.push(`${c.listed} listed`)
  if (c.changed) parts.push(`${c.changed} changed`)
  if (c.closed) parts.push(`${c.closed} closed`)
  if (c.boards_failed) parts.push(`${n(c.boards_failed, 'board')} failed`)
  if (run.mode === 'find_and_apply') {
    parts.push(`${c.apply_queued ?? 0} to apply`)
    if (c.ready_for_review) parts.push(`${c.ready_for_review} ready for review`)
    parts.push(`${c.applied ?? 0} applied`, `${c.failed ?? 0} failed`, `${c.needs_input ?? 0} need input`)
  }
  return parts.join(' · ')
}

type Props = {
  state: RunState | undefined
  profile: Profile | null
  onRun: (body: RunRequest) => Promise<void>
  onSwitch: (s: Switches) => Promise<void>
}

export default function RunBar({ state, profile, onRun, onSwitch }: Props) {
  const [dialog, setDialog] = useState(false)
  const [time, setTime] = useState<string | null>(null)
  const last = state?.last ?? null
  const going = state?.active ?? null
  const queued = state?.queued ?? null
  const extracting = (going ?? last)?.extracting ?? 0
  const at = time ?? (profile ? hhmm(profile.daily_run_at) : '06:00')

  return (
    <section className="card runbar">
      <div className="card-head">
        <h2>Runs</h2>
        <div className="run-controls">
          <label className="switch" title="Find new jobs every morning by itself">
            <input
              type="checkbox"
              role="switch"
              checked={profile?.daily_run ?? false}
              disabled={!profile}
              onChange={(e) => void onSwitch({ daily_run: e.target.checked })}
            />
            <span>Daily run at</span>
          </label>
          <input
            type="time"
            className="time"
            aria-label="Daily run time"
            value={at}
            disabled={!profile}
            onChange={(e) => setTime(e.target.value)}
            onBlur={() => {
              if (profile && time && time !== hhmm(profile.daily_run_at)) void onSwitch({ daily_run_at: time })
              setTime(null)
            }}
          />
          <span className="segmented" role="group" aria-label="Apply mode">
            <button type="button" aria-pressed={profile?.apply_mode !== 'automatic'} disabled={!profile}>
              Review
            </button>
            <button type="button" aria-pressed={profile?.apply_mode === 'automatic'} disabled title="Automatic mode arrives in Phase 8; Review mode submits nothing until you approve">
              Automatic
            </button>
          </span>
          <button type="button" className="primary" onClick={() => setDialog(true)}>
            Run…
          </button>
        </div>
      </div>
      {profile?.daily_run && (
        <p className="hint">
          Daily run is on: every day at {hhmm(profile.daily_run_at)}, finding jobs only. It runs only while this
          computer is awake.
        </p>
      )}
      <p className={last?.status === 'failed' ? 'error' : 'lead'} role="status" data-testid="last-run">
        {last ? (
          <>
            <strong>Last run</strong> {when(last.ended_at ?? last.created_at)}, from the {TRIGGERS[last.trigger]}
            {scopeText(last)}:{' '}
            {last.status === 'failed' ? `failed: ${last.error ?? 'unknown error'}` : counts(last)}
          </>
        ) : state === undefined ? (
          'Loading…'
        ) : (
          'No run yet.'
        )}
      </p>
      {going && (
        <p className="hint" role="status">
          Running now (from the {TRIGGERS[going.trigger]}
          {scopeText(going)}): fetching boards, grouping duplicates and matching your profile…
        </p>
      )}
      {queued && (
        <p className="hint" role="status">
          Queued (from the {TRIGGERS[queued.trigger]}
          {scopeText(queued)}): starts when the run before it ends. More presses join this run.
        </p>
      )}
      {extracting > 0 && (
        <p className="hint" role="status">
          Reading {extracting} job description{extracting === 1 ? '' : 's'} for skills, salary and work mode…
        </p>
      )}
      {last?.counts.board_errors?.map((e) => (
        <p key={e} className="hint error">
          {e}
        </p>
      ))}
      {dialog && (
        <RunDialog
          roles={profile?.target_roles ?? []}
          busy={!!going || !!queued}
          onClose={() => setDialog(false)}
          onStart={async (body) => {
            await onRun(body)
            setDialog(false)
          }}
        />
      )}
    </section>
  )
}
