import { createContext, useContext } from 'react'
import type { AuthMe } from '@/types/api'

export interface AuthContextValue {
  user: AuthMe | null
  loading: boolean
  refetch: () => Promise<AuthMe | null>
  setUser: (me: AuthMe | null) => void
}

export const AuthContext = createContext<AuthContextValue | undefined>(undefined)

/** Read auth state in a route or component. Throws outside AuthProvider. */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (ctx === undefined) {
    throw new Error('useAuth must be used inside <AuthProvider>')
  }
  return ctx
}
