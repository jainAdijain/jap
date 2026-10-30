import { useEffect, useState } from 'react'

import { ago, plural } from './format'
import JobsPage from './pages/JobsPage'
import PreferencesPage from './pages/PreferencesPage'
import ProfilePage from './pages/ProfilePage'
import SettingsPage from './pages/SettingsPage'
import { needsAttention, searching, useApp } from './appContext'
import { go, VIEWS, type View } from './nav'
import { AppProvider } from './store'


const viewOf = (hash: string): View => {
  const v = hash.replace(/^#/, '').split('?')[0] as View
  return VIEWS.includes(v) ? v : 'jobs'
}

const ICONS: Record<View, string> = {
  jobs: 'M3 9a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2',
  profile: 'M12 4a4 4 0 1 1 0 8 4 4 0 0 1 0-8zM4 21c1-4 4-6 8-6s7 2 8 6',
  prefs: 'M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M14 6a2 2 0 1 0 4 0 2 2 0 1 0-4 0M8 12a2 2 0 1 0 4 0 2 2 0 1 0-4 0M16 18a2 2 0 1 0 4 0 2 2 0 1 0-4 0',
  settings: 'M12 9a3 3 0 1 1 0 6 3 3 0 0 1 0-6zM12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1',
}
const LABELS: Record<View, string> = { jobs: 'Jobs', profile: 'Profile', prefs: 'Job preferences', settings: 'Settings' }

function nextSearch(at: string): string {
  const [h, m] = at.split(':').map(Number)
  const now = new Date()
  const next = new Date(now)
  next.setHours(h, m, 0, 0)
  const today = next > now
  return `${today ? 'today' : 'tomorrow'} ${at.slice(0, 5)}`
}

function Shell() {
  const [view, setView] = useState<View>(() => viewOf(window.location.hash))
  const { profile, summary, runs, health, healthError, ops } = useApp()
  useEffect(() => {
    const onHash = () => {
      setView(viewOf(window.location.hash))
      window.scrollTo(0, 0)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  // "/" jumps to the Jobs search box.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (document.activeElement?.tagName ?? '').toUpperCase()
      if (e.key === '/' && !['INPUT', 'SELECT', 'TEXTAREA'].includes(tag)) {
        e.preventDefault()
        go('jobs')
        setTimeout(() => document.getElementById('job-search')?.focus(), 0)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const needsYou = summary?.totals.needs_input ?? 0
  const attention = needsAttention(health, healthError, ops)
  const last = runs?.last
  const busy = searching(runs)
  return (
    <div className="app">
      <aside className="side">
        <div className="logo">
          <span className="logo-mark">JAP</span>
          <span className="logo-name">
            Job Application Pipeline<small>Version 0.11.0</small>
          </span>
        </div>
        <nav aria-label="Sections">
          {VIEWS.map((v) => (
            <a key={v} href={`#${v}`} aria-current={view === v ? 'page' : undefined}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d={ICONS[v]} />
              </svg>
              {LABELS[v]}
              {v === 'jobs' && needsYou > 0 && (
                <span className="badge" title={`${plural(needsYou, 'job')} need you`}>
                  {needsYou}
                </span>
              )}
              {v === 'settings' && attention && <span className="alert-dot" title="Something in System status needs attention" />}
            </a>
          ))}
        </nav>
        <div className="side-foot">
          <span>
            <span className={busy ? 'live busy' : 'live'} />
            <b>{busy ? 'Searching for new jobs…' : last ? `Updated ${ago(last.ended_at ?? last.created_at)}` : 'No search yet'}</b>
          </span>
          {profile && <span>{profile.daily_run ? `Next search ${nextSearch(profile.daily_run_at)}` : 'Daily search is off'}</span>}
          {profile && <span>Applying: {profile.apply_mode === 'automatic' ? 'JAP sends for you' : 'you send each one'}</span>}
        </div>
      </aside>
      <div className="work">
        {view === 'jobs' && <JobsPage />}
        {view === 'profile' && <ProfilePage />}
        {view === 'prefs' && <PreferencesPage />}
        {view === 'settings' && <SettingsPage />}
      </div>
    </div>
  )
}

export default function App() {
  return (
    <AppProvider>
      <Shell />
    </AppProvider>
  )
}
