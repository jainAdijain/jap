import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import { api, type Board, type Condition, type MatchPreview, type WorkMode } from '../api/client'
import TagInput from '../components/TagInput'
import { atsLabel, errorText, plural } from '../format'
import { useApp } from '../appContext'
import { useProfileForm } from './profileForm'

type Remote = 'none' | 'mine' | 'all'
const PRESETS: { key: string; rule: Condition[][]; title: ReactNode; note: string }[] = [
  { key: 'or', rule: [['role'], ['location']], title: <>Job title <b>or</b> location fits</>, note: 'More jobs, some less relevant.' },
  { key: 'and', rule: [['location', 'role']], title: <>Job title <b>and</b> location fit</>, note: 'Recommended.' },
  { key: 'skills', rule: [['location', 'role', 'skills']], title: <>Job title and location fit, <b>and</b> it asks for your required skills</>, note: 'Fewest jobs.' },
]
const WORDS: Record<Condition, string> = { role: 'job title', location: 'location', skills: 'required skills' }
const key = (rule: Condition[][]) => JSON.stringify(rule.map((c) => [...c].sort()).sort())
const REMOTE_WORD = /^(remote|anywhere|worldwide|global|wfh|work from home)$/i

const joinOr = (items: string[]) => (items.length <= 1 ? (items[0] ?? '') : `${items.slice(0, -1).join(', ')} or ${items[items.length - 1]}`)

function Companies() {
  const { toast } = useApp()
  const [boards, setBoards] = useState<Board[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [all, setAll] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => setBoards(await api.boards()), [])
  useEffect(() => {
    load().catch((err) => setError(errorText(err)))
  }, [load])
  const add = async () => {
    setBusy(true)
    setError(null)
    try {
      const d = await api.detectBoard(text.trim())
      if (d.already_watched) {
        toast(`You already follow ${d.display_name}.`)
      } else {
        await api.addBoard({ ats: d.ats, board_token: d.board_token, display_name: d.display_name })
        toast(`Following ${d.display_name} on ${atsLabel(d.ats)}: ${plural(d.job_count, 'open job')}. They show after the next search.`)
        await load()
      }
      setText('')
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }
  const remove = async (b: Board) => {
    await api.removeBoard(b.id)
    await load()
    toast({
      text: `Stopped following ${b.display_name}. Its jobs are hidden.`,
      undo: () => void api.addBoard({ ats: b.ats, board_token: b.board_token, display_name: b.display_name }).then(load),
    })
  }
  const shown = all ? boards : boards.slice(0, 12)
  const broken = boards.filter((b) => b.last_ok === false)
  return (
    <div className="sec">
      <div className="sec-l">
        <h2>Companies to follow</h2>
        <p>JAP searches these companies’ career pages. Greenhouse, Lever and Ashby are supported. Changes here save straight away.</p>
      </div>
      <div className="sec-r">
        <form
          className="inline"
          onSubmit={(e) => {
            e.preventDefault()
            if (text.trim()) void add()
          }}
        >
          <input type="text" style={{ flex: '1 1 260px', width: 'auto' }} placeholder="Paste a careers page link or type a company name" aria-label="Add a company" value={text} onChange={(e) => setText(e.target.value)} />
          <button type="submit" className="btn-primary" disabled={!text.trim() || busy}>
            {busy ? 'Looking…' : 'Add'}
          </button>
        </form>
        {error && <p className="note-box bad">{error}</p>}
        {boards.length === 0 ? (
          <p className="muted">No companies yet. Add the first one above.</p>
        ) : (
          <div className="tags bare">
            {shown.map((b) => (
              <span key={b.id} data-ats={b.ats} className={b.last_ok === false ? 'tag warn' : 'tag'} title={b.last_ok === false ? `${atsLabel(b.ats)} board “${b.board_token}” could not be read at the last search` : `${atsLabel(b.ats)} · ${b.board_token}`}>
                {b.display_name}
                <span className="muted">&nbsp;· {atsLabel(b.ats)}</span>
                <button type="button" aria-label={`Stop following ${b.display_name}`} onClick={() => void remove(b)}>
                  ×
                </button>
              </span>
            ))}
            {boards.length > 12 && (
              <span className="muted small">
                {all ? '' : `and ${boards.length - 12} more · `}
                <button type="button" className="btn-link" onClick={() => setAll(!all)}>
                  {all ? 'Show fewer' : 'Show all'}
                </button>
              </span>
            )}
          </div>
        )}
        {broken.length > 0 && (
          <p className="help warn-text">
            {joinOr(broken.map((b) => b.display_name))} could not be read at the last search. Check the link, or remove {broken.length === 1 ? 'it' : 'them'}.
          </p>
        )}
      </div>
    </div>
  )
}

export default function PreferencesPage() {
  const { summary, toast } = useApp()
  const { profile, form, set, setForm, dirty, saving, error, save, discard } = useProfileForm()
  const [preview, setPreview] = useState<MatchPreview | null>(null)

  const formKey = form ? JSON.stringify(form) : ''
  useEffect(() => {
    if (!form || !dirty || form.work_modes.length === 0) {
      setPreview(null)
      return
    }
    const ctrl = new AbortController()
    const t = setTimeout(() => {
      api.previewProfile(form, ctrl.signal).then(setPreview, () => setPreview(null))
    }, 350)
    return () => {
      clearTimeout(t)
      ctrl.abort()
    }
    // formKey stands for form, so the preview runs once per actual change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [formKey, dirty])

  const suggestions = useMemo(() => {
    const mine = new Set((form?.locations ?? []).map((l) => l.toLowerCase().split(',')[0].trim()))
    const out: string[] = []
    for (const l of summary?.locations ?? []) {
      const city = l.split(/,|\s-\s/)[0].trim()
      if (!city || /remote/i.test(l) || mine.has(city.toLowerCase()) || out.includes(city)) continue
      out.push(city)
    }
    return out.slice(0, 6)
  }, [summary?.locations, form?.locations])

  if (!form) {
    return (
      <section className="page">
        <p className="muted">Loading…</p>
      </section>
    )
  }

  const modes = form.work_modes
  const remote: Remote = !modes.includes('remote') ? 'none' : form.remote_any ? 'all' : 'mine'
  const setRemote = (r: Remote) => {
    const office = modes.filter((m) => m !== 'remote')
    setForm({ ...form, work_modes: r === 'none' ? office : [...office, 'remote'], remote_any: r === 'all' })
  }
  const toggleOffice = (m: WorkMode) => set('work_modes', modes.includes(m) ? modes.filter((x) => x !== m) : [...modes, m])
  const cities = form.locations.filter((l) => !REMOTE_WORD.test(l.trim()))
  const office = [modes.includes('onsite') && 'on-site', modes.includes('hybrid') && 'hybrid'].filter(Boolean).join(' and ')
  const officePart = office ? `${office} jobs${cities.length ? ` in ${joinOr(cities)}` : ' (add a city so they show)'}` : ''
  const remotePart =
    remote === 'none' ? '' : remote === 'all' ? 'every remote job' : `remote jobs open to ${joinOr([...form.remote_regions, 'anywhere'])}`
  const readback =
    !officePart && !remotePart
      ? 'Tick On-site or Hybrid, or show remote jobs: as it is, no job can show.'
      : `You will see ${[officePart, remotePart].filter(Boolean).join(', and ')}.${remote === 'none' ? ' No remote jobs.' : ''}`
  const presetKey = PRESETS.find((p) => key(p.rule) === key(form.match_rule))?.key ?? 'custom'
  const custom = form.match_rule.map((c) => c.map((x) => WORDS[x]).join(' and ')).join(', or ')
  const savedRule = profile && !PRESETS.some((p) => key(p.rule) === key(profile.match_rule)) ? profile.match_rule : null
  const now = summary ? summary.totals.listings : null

  return (
    <section className="page">
      <div className="page-head">
        <div>
          <h1>Job preferences</h1>
          <p>Decides which jobs appear in Jobs. Changes apply to the jobs already found too.</p>
        </div>
      </div>

      <div className="sec">
        <div className="sec-l">
          <h2>Job titles</h2>
          <p>A job shows when its title matches one of these. “Engineer” also matches “Developer”, and “Sr” matches “Senior”.</p>
        </div>
        <div className="sec-r">
          <TagInput label="Add a job title" placeholder="Add a job title, e.g. Platform Engineer" items={form.target_roles} onChange={(v) => set('target_roles', v)} />
        </div>
      </div>

      <div className="sec">
        <div className="sec-l">
          <h2>Skills</h2>
          <p>Filled in from your resume. Required skills count when Matching asks for skills; nice-to-have ones show on jobs that ask for them.</p>
        </div>
        <div className="sec-r">
          <div className="two">
            <div className="f">
              <span className="lab">Required</span>
              <TagInput label="Add a required skill" placeholder="Add a skill" lower items={form.must_have_skills} onChange={(v) => set('must_have_skills', v)} />
            </div>
            <div className="f">
              <span className="lab">Nice to have</span>
              <TagInput label="Add a nice-to-have skill" placeholder="Add a skill" lower items={form.nice_to_have_skills} onChange={(v) => set('nice_to_have_skills', v)} />
            </div>
          </div>
        </div>
      </div>

      <div className="sec">
        <div className="sec-l">
          <h2>Locations</h2>
          <p>Where you can work. Office and remote jobs are set separately, so each one is clear.</p>
        </div>
        <div className="sec-r">
          <div className="f">
            <span className="lab">Office jobs in</span>
            <div className="inline">
              <label className="inline">
                <input type="checkbox" checked={modes.includes('onsite')} onChange={() => toggleOffice('onsite')} /> On-site
              </label>
              <label className="inline">
                <input type="checkbox" checked={modes.includes('hybrid')} onChange={() => toggleOffice('hybrid')} /> Hybrid
              </label>
            </div>
            <TagInput label="Add a city" placeholder="Add a city or country" items={cities} onChange={(v) => set('locations', v)} />
            {suggestions.length > 0 && (
              <div className="suggest">
                In your current jobs:
                {suggestions.map((s) => (
                  <button key={s} type="button" onClick={() => set('locations', [...cities, s])}>
                    + {s}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="f">
            <span className="lab" id="remote-label">
              Remote jobs
            </span>
            <div className="radio-list" role="radiogroup" aria-labelledby="remote-label">
              <label>
                <input type="radio" name="remote" checked={remote === 'none'} onChange={() => setRemote('none')} />
                <span>Don&apos;t show remote jobs</span>
              </label>
              <label>
                <input type="radio" name="remote" checked={remote === 'mine'} onChange={() => setRemote('mine')} />
                <span>Only remote jobs open to these countries or regions</span>
                {remote === 'mine' && (
                  <span className="more" onClick={(e) => e.preventDefault()}>
                    <TagInput label="Add a country or region" placeholder="Add a country or region, e.g. India or APAC" fixed={['Anywhere (always)']} items={form.remote_regions} onChange={(v) => set('remote_regions', v)} />
                    <span className="help">Jobs open to anywhere in the world always count.</span>
                  </span>
                )}
              </label>
              <label>
                <input type="radio" name="remote" checked={remote === 'all'} onChange={() => setRemote('all')} />
                <span>All remote jobs</span>
                <span className="d">Whatever countries the job is open to.</span>
              </label>
            </div>
          </div>
          <p className={modes.length === 0 ? 'readback warn-text' : 'readback'} role="status">
            {readback}
          </p>
        </div>
      </div>

      <Companies />

      <div className="sec">
        <div className="sec-l">
          <h2>Hide jobs</h2>
          <p>Optional. Jobs that match these never appear.</p>
        </div>
        <div className="sec-r">
          <div className="two">
            <div className="f">
              <span className="lab">From these companies</span>
              <TagInput label="Add a company to hide" placeholder="Add a company" items={form.excluded_companies} onChange={(v) => set('excluded_companies', v)} />
            </div>
            <div className="f">
              <span className="lab">With these words in the title</span>
              <TagInput label="Add a word to hide" placeholder="e.g. Intern" items={form.excluded_title_words} onChange={(v) => set('excluded_title_words', v)} />
            </div>
          </div>
        </div>
      </div>

      <div className="sec">
        <div className="sec-l">
          <h2>Matching</h2>
          <p>How strictly a job has to fit.</p>
        </div>
        <div className="sec-r">
          <div className="radio-list" role="radiogroup" aria-label="Matching">
            {PRESETS.map((p) => (
              <label key={p.key}>
                <input type="radio" name="match" checked={presetKey === p.key} onChange={() => set('match_rule', p.rule)} />
                <span>{p.title}</span>
                <span className="d">{p.note}</span>
              </label>
            ))}
            {(presetKey === 'custom' || savedRule) && (
              <label>
                <input type="radio" name="match" checked={presetKey === 'custom'} onChange={() => savedRule && set('match_rule', savedRule)} />
                <span>Custom: {(presetKey === 'custom' ? custom : savedRule!.map((c) => c.map((x) => WORDS[x]).join(' and ')).join(', or ')).replace(/^./, (c) => c.toUpperCase())}</span>
                <span className="d">The rule you set before. Pick one of the choices above to make it simpler.</span>
              </label>
            )}
          </div>
        </div>
      </div>

      {(dirty || error) && (
        <div className="savebar">
          <div className="in">
            {error ? (
              <span className="error-text" role="alert">
                {error}
              </span>
            ) : modes.length === 0 ? (
              <span className="warn-text">Tick On-site or Hybrid, or show remote jobs, before you save.</span>
            ) : (
              <span role="status">
                {preview ? (
                  <>
                    <span className="n">{preview.matching_count}</span> {preview.matching_count === 1 ? 'job fits' : 'jobs fit'} these preferences
                  </>
                ) : (
                  'Counting the jobs that fit…'
                )}{' '}
                <span className="muted">
                  {now !== null && preview ? `(${now} now). ` : ''}Saving also starts a search for new jobs.
                </span>
              </span>
            )}
            <span className="inline">
              <button type="button" disabled={saving} onClick={discard}>
                Discard
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={!dirty || saving || modes.length === 0}
                onClick={() =>
                  void save().then((n) => n !== null && toast(`Saved. ${plural(n, 'job fits', 'jobs fit')} now. JAP is searching for new ones.`))
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
