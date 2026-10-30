import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'

import { api, type Health, type ListingSummary, type Ops, type Profile, type Resume, type RunState } from './api/client'
import { Ctx, searching, type Store, type Toast } from './appContext'

const SEARCH_POLL_MS = 2000 // while a search is going, so its line changes as it happens
const POLL_MS = 5000 // while descriptions are read or an application is going
const IDLE_POLL_MS = 30000 // notices a daily search or a search started by a save

export function AppProvider({ children }: { children: ReactNode }) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [summary, setSummary] = useState<ListingSummary | null>(null)
  const [runs, setRuns] = useState<RunState | undefined>(undefined)
  const [health, setHealth] = useState<Health | null>(null)
  const [healthError, setHealthError] = useState<string | null>(null)
  const [ops, setOps] = useState<Ops | null>(null)
  const [resume, setResume] = useState<Resume | null>(null)
  const [tick, setTick] = useState(0)
  const [current, setToast] = useState<Toast | null>(null)
  const timer = useRef<number | undefined>(undefined)

  const refresh = useCallback(async () => {
    const [state, totals] = await Promise.all([api.runState(), api.summary()])
    setRuns(state)
    setSummary(totals)
    setTick((t) => t + 1)
  }, [])
  const checkHealth = useCallback(async () => {
    try {
      setHealth(await api.health())
      setHealthError(null)
    } catch (err) {
      setHealth(null)
      setHealthError(err instanceof Error ? err.message : String(err))
    }
    api.ops().then(setOps, () => setOps(null))
  }, [])

  useEffect(() => {
    api.profile().then(setProfile, () => undefined)
    api.resumes().then((v) => setResume(v.find((r) => r.is_active) ?? null), () => undefined)
    void refresh().catch(() => undefined)
    void checkHealth()
    const t = setInterval(() => void checkHealth(), 10000)
    return () => clearInterval(t)
  }, [refresh, checkHealth])

  // New jobs appear without a reload: poll while a search is going, and now and then otherwise.
  const busy = searching(runs) || (summary?.totals.applying ?? 0) > 0
  const fast = !!runs?.active || !!runs?.queued
  const lastId = runs?.last?.id
  useEffect(() => {
    if (busy) {
      const t = setInterval(() => void refresh().catch(() => undefined), fast ? SEARCH_POLL_MS : POLL_MS)
      return () => clearInterval(t)
    }
    const t = setInterval(() => {
      api
        .runState()
        .then((s) => (searching(s) || s.last?.id !== lastId ? refresh() : undefined))
        .catch(() => undefined)
    }, IDLE_POLL_MS)
    return () => clearInterval(t)
  }, [busy, fast, lastId, refresh])

  const toast = useCallback((t: Toast | string) => {
    setToast(typeof t === 'string' ? { text: t } : t)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setToast(null), 6000)
  }, [])

  const store: Store = { profile, setProfile, summary, runs, health, healthError, ops, resume, tick, refresh, toast }
  // Keep the active resume fresh after an upload on Profile.
  useEffect(() => {
    api.resumes().then((v) => setResume(v.find((r) => r.is_active) ?? null), () => undefined)
  }, [profile?.must_have_skills, profile?.nice_to_have_skills])

  return (
    <Ctx.Provider value={store}>
      {children}
      {current && (
        <div className={current.bad ? 'toast bad' : 'toast'} role="status">
          <span>{current.text}</span>
          {current.undo && (
            <button
              type="button"
              onClick={() => {
                current.undo?.()
                setToast(null)
              }}
            >
              Undo
            </button>
          )}
          <button type="button" aria-label="Dismiss" onClick={() => setToast(null)}>
            ×
          </button>
        </div>
      )}
    </Ctx.Provider>
  )
}
