import { useCallback, useEffect, useState } from 'react'

import { auth, SIGNED_OUT_EVENT, type AuthState, type User } from './api/client'
import { AuthCtx, useAuth } from './authContext'
import { ago, errorText, plural } from './format'
import JobsPage from './pages/JobsPage'
import PreferencesPage from './pages/PreferencesPage'
import ProfilePage from './pages/ProfilePage'
import SettingsPage from './pages/SettingsPage'
import SignInPage from './pages/SignInPage'
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
  const { user, signOut } = useAuth()
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
            Job Application Pipeline<small>Version 0.13.0</small>
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
          <span className="side-user">
            <span title={user.email}>{user.email}</span>
            <button type="button" className="btn-link side-signout" onClick={() => void signOut()}>
              Sign out
            </button>
          </span>
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

/** Sign-in comes first (PRD S6): nothing else loads until there is a signed-in session. */
export default function App() {
  const [state, setState] = useState<AuthState | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const load = useCallback(() => {
    auth.state().then(
      (s) => {
        setState(s)
        setFailed(null)
      },
      (err) => setFailed(errorText(err)),
    )
  }, [])
  useEffect(load, [load])
  // Any API call that finds the session gone (expired, or signed out elsewhere) returns here.
  useEffect(() => {
    const onSignedOut = () => setState((s) => (s && s.user ? { ...s, user: null, two_step_pending: false } : s))
    window.addEventListener(SIGNED_OUT_EVENT, onSignedOut)
    return () => window.removeEventListener(SIGNED_OUT_EVENT, onSignedOut)
  }, [])
  const setUser = useCallback((user: User) => setState((s) => (s ? { ...s, user, has_accounts: true, sign_up_open: false } : s)), [])
  const signOut = useCallback(async (everywhere = false) => {
    await (everywhere ? auth.signOutEverywhere() : auth.signOut()).catch(() => undefined)
    setState((s) => (s ? { ...s, user: null, two_step_pending: false } : s))
  }, [])

  if (failed) {
    return (
      <main className="auth">
        <div className="auth-card">
          <h1>JAP is not answering</h1>
          <p className="muted">{failed}</p>
          <button type="button" className="btn-primary auth-submit" onClick={load}>
            Try again
          </button>
        </div>
      </main>
    )
  }
  if (!state) return null
  if (!state.user) return <SignInPage state={state} onSignedIn={setUser} />
  return (
    <AuthCtx.Provider value={{ user: state.user, setUser, signOut }}>
      <AppProvider>
        <Shell />
      </AppProvider>
    </AuthCtx.Provider>
  )
}
