import { useCallback, useEffect, useState } from 'react'

import { getHealth, type Health } from '../api/client'
import StatusRow from '../components/StatusRow'

const SERVICES: { key: string; name: string }[] = [
  { key: 'api', name: 'API' },
  { key: 'database', name: 'Database' },
  { key: 'inngest', name: 'Inngest' },
  { key: 'ollama', name: 'Ollama' },
]
const REFRESH_MS = 5000

export default function StatusPage() {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [checkedAt, setCheckedAt] = useState<Date | null>(null)

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      setHealth(await getHealth(signal))
      setError(null)
    } catch (err) {
      if (signal?.aborted) return
      setHealth(null)
      setError(err instanceof Error ? err.message : String(err))
    }
    setCheckedAt(new Date())
  }, [])

  useEffect(() => {
    const ctrl = new AbortController()
    void refresh(ctrl.signal)
    const timer = setInterval(() => void refresh(ctrl.signal), REFRESH_MS)
    return () => {
      ctrl.abort()
      clearInterval(timer)
    }
  }, [refresh])

  const rowFor = (key: string) => {
    if (error) {
      return key === 'api'
        ? { ok: false, detail: error }
        : { ok: false, detail: 'Unknown while the API is down' }
    }
    const check = health?.checks[key]
    return check ?? { ok: null, detail: '' }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h1>Status</h1>
        <button type="button" onClick={() => void refresh()}>
          Check now
        </button>
      </div>
      <ul className="status-list">
        {SERVICES.map((s) => {
          const row = rowFor(s.key)
          return <StatusRow key={s.key} name={s.name} ok={row.ok} detail={row.detail} />
        })}
      </ul>
      <p className="muted">
        {checkedAt ? `Last checked ${checkedAt.toLocaleTimeString()}, refreshes every 5 seconds.` : 'Checking…'}
      </p>
    </section>
  )
}
