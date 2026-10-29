import { useState } from 'react'

import DashboardPage from './pages/DashboardPage'
import ProfilePage from './pages/ProfilePage'
import StatusPage from './pages/StatusPage'

type Tab = 'dashboard' | 'profile'

export default function App() {
  const [tab, setTab] = useState<Tab>('dashboard')
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
            <DashboardPage />
            <StatusPage />
          </>
        ) : (
          <ProfilePage />
        )}
      </main>
    </div>
  )
}
