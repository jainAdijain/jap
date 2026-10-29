import { useState } from 'react'

import ProfilePage from './pages/ProfilePage'
import StatusPage from './pages/StatusPage'

type Tab = 'dashboard' | 'profile'

export default function App() {
  const [tab, setTab] = useState<Tab>('profile')
  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">JAP</span>
        <span className="tagline">Job Application Pipeline</span>
      </header>
      <nav className="tabs" aria-label="Sections">
        {(
          [
            ['dashboard', 'Dashboard'],
            ['profile', 'My profile'],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={tab === key ? 'tab active' : 'tab'}
            aria-current={tab === key ? 'page' : undefined}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </nav>
      <main>
        {tab === 'dashboard' ? (
          <>
            <section className="card">
              <h1>Dashboard</h1>
              <p className="muted">
                Jobs show up here from Phase 2, once JAP fetches the boards in Companies to watch.
              </p>
            </section>
            <StatusPage />
          </>
        ) : (
          <ProfilePage />
        )}
      </main>
    </div>
  )
}
