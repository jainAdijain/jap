import { createContext, useContext } from 'react'

import type { User } from './api/client'

export type Auth = {
  user: User
  setUser: (u: User) => void
  /** Sign out of this browser (everywhere: every browser) and show sign-in. */
  signOut: (everywhere?: boolean) => Promise<void>
}

export const AuthCtx = createContext<Auth | null>(null)

export function useAuth(): Auth {
  const value = useContext(AuthCtx)
  if (!value) throw new Error('useAuth outside the signed-in app')
  return value
}
