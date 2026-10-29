import StatusPage from './pages/StatusPage'

export default function App() {
  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">JAP</span>
        <span className="tagline">Job Application Pipeline</span>
      </header>
      <main>
        <StatusPage />
      </main>
    </div>
  )
}
