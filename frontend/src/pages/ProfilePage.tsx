import { useEffect, useState } from 'react'

import { api, type Link, type Profile, type ProfileFields, type WorkMode } from '../api/client'
import AnswerBank from '../components/AnswerBank'
import BoardsSection from '../components/BoardsSection'
import ChipList from '../components/ChipList'
import MatchRule from '../components/MatchRule'
import ResumeCard from '../components/ResumeCard'

const MODES: { key: WorkMode; label: string }[] = [
  { key: 'remote', label: 'Remote' },
  { key: 'hybrid', label: 'Hybrid' },
  { key: 'onsite', label: 'Onsite' },
]

const fields = (p: Profile): ProfileFields => ({
  full_name: p.full_name,
  email: p.email,
  phone: p.phone,
  address: p.address,
  links: p.links,
  target_roles: p.target_roles,
  must_have_skills: p.must_have_skills,
  nice_to_have_skills: p.nice_to_have_skills,
  locations: p.locations,
  remote_regions: p.remote_regions,
  match_rule: p.match_rule,
  work_modes: p.work_modes,
  excluded_companies: p.excluded_companies,
  excluded_title_words: p.excluded_title_words,
  tone_note: p.tone_note,
})

const money = (n: number) => `$${n < 1 ? n.toFixed(2) : n.toFixed(2).replace(/\.00$/, '')}`

export default function ProfilePage() {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [form, setForm] = useState<ProfileFields | null>(null)
  const [message, setMessage] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)
  const [saving, setSaving] = useState(false)

  const load = async () => {
    const p = await api.profile()
    setProfile(p)
    setForm(fields(p))
  }
  useEffect(() => {
    load().catch((err) => setMessage({ kind: 'error', text: `Couldn't load your profile: ${err}` }))
  }, [])

  if (!form || !profile) {
    return <section className="card">{message ? <p className="error">{message.text}</p> : <p className="muted">Loading…</p>}</section>
  }

  const set = <K extends keyof ProfileFields>(key: K, value: ProfileFields[K]) => {
    setForm({ ...form, [key]: value })
    setMessage(null)
  }
  const dirty = JSON.stringify(form) !== JSON.stringify(fields(profile))

  const save = async () => {
    setSaving(true)
    try {
      const saved = await api.saveProfile(form)
      setProfile(saved)
      setForm(fields(saved))
      setMessage({
        kind: 'ok',
        text: `Saved. ${saved.matching_count} listing${saved.matching_count === 1 ? '' : 's'} match now.`,
      })
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof Error ? err.message : String(err) })
    } finally {
      setSaving(false)
    }
  }

  const setLink = (i: number, link: Link) => set('links', form.links.map((l, j) => (j === i ? link : l)))

  return (
    <div className="profile">
      <p className="lead">
        The pipeline only picks up jobs that match the roles, skills and locations below. Your
        active resume is attached to every application.
      </p>

      <ResumeCard
        mustHave={form.must_have_skills}
        niceToHave={form.nice_to_have_skills}
        onAccepted={(must, nice) => {
          // Accepting saves the skills on the server; keep any other unsaved edits.
          setForm({ ...form, must_have_skills: must, nice_to_have_skills: nice })
          setProfile({ ...profile, must_have_skills: must, nice_to_have_skills: nice })
        }}
      />

      <section className="card">
        <h2>Contact details</h2>
        <p className="hint">Filled into application forms by code. They never go to an AI model.</p>
        <div className="grid">
          <label>
            Full name
            <input value={form.full_name ?? ''} onChange={(e) => set('full_name', e.target.value || null)} />
          </label>
          <label>
            Email
            <input type="email" value={form.email ?? ''} onChange={(e) => set('email', e.target.value || null)} />
          </label>
          <label>
            Phone
            <input type="tel" value={form.phone ?? ''} onChange={(e) => set('phone', e.target.value || null)} />
          </label>
          <label className="wide">
            Address
            <textarea rows={2} value={form.address ?? ''} onChange={(e) => set('address', e.target.value || null)} />
          </label>
        </div>
        <fieldset className="field">
          <legend>Links</legend>
          {form.links.map((link, i) => (
            <div key={i} className="add-row">
              <input aria-label="Link label" placeholder="LinkedIn" value={link.label} onChange={(e) => setLink(i, { ...link, label: e.target.value })} />
              <input aria-label="Link URL" placeholder="https://" value={link.url} onChange={(e) => setLink(i, { ...link, url: e.target.value })} />
              <button type="button" aria-label={`Remove ${link.label || 'link'}`} onClick={() => set('links', form.links.filter((_, j) => j !== i))}>
                Remove
              </button>
            </div>
          ))}
          <button type="button" onClick={() => set('links', [...form.links, { label: '', url: '' }])}>
            Add a link
          </button>
        </fieldset>
      </section>

      <section className="card">
        <h2>What to look for</h2>
        <ChipList title="Target roles" hint="A job title has to match one of these." placeholder="Add a role, e.g. Platform Engineer" items={form.target_roles} onChange={(v) => set('target_roles', v)} />
        <ChipList title="Must-have skills" hint="Suggested from your resume. Matching and cover letters use these first." placeholder="Add a skill, e.g. terraform" items={form.must_have_skills} onChange={(v) => set('must_have_skills', v)} />
        <ChipList title="Nice-to-have skills" placeholder="Add a skill" items={form.nice_to_have_skills} onChange={(v) => set('nice_to_have_skills', v)} />
        <ChipList title="Locations" hint="Cities, countries or regions you are open to." placeholder="Add a city or Remote" items={form.locations} onChange={(v) => set('locations', v)} />
        <ChipList title="Remote regions" hint="For remote jobs: where you can be hired from. Remote - Worldwide always counts." placeholder="e.g. India or APAC" items={form.remote_regions} onChange={(v) => set('remote_regions', v)} />
        <MatchRule rule={form.match_rule} onChange={(v) => set('match_rule', v)} />
        <fieldset className="field">
          <legend>Work mode</legend>
          <p className="hint">Which kinds of jobs to apply to. Tick at least one.</p>
          <div className="checks">
            {MODES.map(({ key, label }) => (
              <label key={key} className="check">
                <input
                  type="checkbox"
                  checked={form.work_modes.includes(key)}
                  onChange={() =>
                    set('work_modes', form.work_modes.includes(key) ? form.work_modes.filter((m) => m !== key) : [...form.work_modes, key])
                  }
                />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
      </section>

      <section className="card">
        <h2>Exclusions</h2>
        <ChipList title="Excluded companies" placeholder="Add a company" items={form.excluded_companies} onChange={(v) => set('excluded_companies', v)} />
        <ChipList title="Excluded title words" hint="Jobs whose title contains one of these are dropped." placeholder="e.g. Intern" items={form.excluded_title_words} onChange={(v) => set('excluded_title_words', v)} />
      </section>

      <BoardsSection />
      <AnswerBank />

      <section className="card">
        <h2>Cover letter tone</h2>
        <label className="sr-only" htmlFor="tone">Tone note</label>
        <textarea id="tone" rows={2} placeholder='e.g. "concise, no buzzwords"' value={form.tone_note ?? ''} onChange={(e) => set('tone_note', e.target.value || null)} />
      </section>

      <div className="savebar">
        <span className={profile.spend.reached ? 'spend error' : 'spend'}>
          AI spend this month: {money(profile.spend.month_usd)} of {money(profile.spend.cap_usd)}
          {profile.spend.reached && ' (cap reached)'}
        </span>
        {message && (
          <span className={message.kind === 'ok' ? 'saved' : 'error'} role="status">
            {message.text}
          </span>
        )}
        <span className="row-buttons">
          <button type="button" disabled={!dirty || saving} onClick={() => setForm(fields(profile))}>
            Cancel
          </button>
          <button type="button" className="primary" disabled={!dirty || saving} onClick={() => void save()}>
            {saving ? 'Saving…' : 'Save profile'}
          </button>
        </span>
      </div>
    </div>
  )
}
