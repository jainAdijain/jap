import { createContext, useContext } from 'react'

import type { Health, ListingSummary, Ops, Profile, Resume, RunState } from './api/client'

export type Toast = { text: string; undo?: () => void; bad?: boolean }

export type Store = {
  profile: Profile | null
  setProfile: (p: Profile) => void
  summary: ListingSummary | null
  runs: RunState | undefined
  health: Health | null
  healthError: string | null
  ops: Ops | null
  resume: Resume | null // the active version, attached to every application
  /** Bumped by every refresh, so pages reload their own lists too. */
  tick: number
  refresh: () => Promise<void>
  toast: (t: Toast | string) => void
}

export const Ctx = createContext<Store | null>(null)

export function useApp(): Store {
  const store = useContext(Ctx)
  if (!store) throw new Error('useApp outside AppProvider')
  return store
}

export const searching = (s: RunState | undefined) =>
  !!s && (!!s.active || !!s.queued || (s.last?.extracting ?? 0) > 0)

/** Something on Settings needs attention: a service is down, no backup today, a form check failed. */
export function needsAttention(health: Health | null, healthError: string | null, ops: Ops | null): boolean {
  if (healthError) return true
  if (health && Object.entries(health.checks).some(([k, c]) => !c.ok && k !== 'ollama')) return true
  if (ops && (!ops.backup_today || ops.form_checks.some((c) => !c.ok))) return true
  return false
}

