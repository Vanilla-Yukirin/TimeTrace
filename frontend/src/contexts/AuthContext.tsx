import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  type ReactNode,
} from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, UnauthorizedError } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import type { AuthMe } from '@/types/api'

interface AuthContextValue {
  /** The current logged-in user, or null if not authed. */
  user: AuthMe | null
  /** True while the initial /v1/auth/me probe is in flight. */
  loading: boolean
  /** Manually re-query /v1/auth/me — call after login / change-password. */
  refresh: () => void
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

/** Wraps the app, polls /v1/auth/me, and listens for cross-tab 401 kicks. */
export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()

  // The "am I logged in?" probe. 401 surfaces as a thrown UnauthorizedError
  // (see lib/api.ts) which react-query treats as a query failure — we then
  // expose ``user: null`` so RequireAuth can redirect.
  // - retry: false → don't keep hammering /auth/me on 401
  // - refetchOnWindowFocus: true → if the user comes back after the session
  //   silently expired on the server, the next focus refetches and kicks
  //   them out instead of letting them click around dead UI
  const meQuery = useQuery({
    queryKey: queryKeys.authMe(),
    queryFn: api.me,
    retry: false,
    refetchOnWindowFocus: true,
    staleTime: 30_000,
  })

  const user: AuthMe | null = meQuery.data ?? null

  const refresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.authMe() })
  }, [queryClient])

  // Cross-tab logout: another tab caught a 401 and broadcast via
  // localStorage['tt_auth_kicked']. Re-check our own session state.
  useEffect(() => {
    const handler = (e: StorageEvent) => {
      if (e.key === 'tt_auth_kicked') {
        // Drop the cached me() so RequireAuth re-evaluates. Don't navigate
        // here — RequireAuth owns the redirect logic so we stay in one place.
        queryClient.removeQueries({ queryKey: queryKeys.authMe() })
        refresh()
      }
    }
    window.addEventListener('storage', handler)
    return () => window.removeEventListener('storage', handler)
  }, [queryClient, refresh])

  const value = useMemo<AuthContextValue>(
    () => ({ user, loading: meQuery.isLoading, refresh }),
    [user, meQuery.isLoading, refresh],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/** Read auth state in a route or component. Throws if used outside AuthProvider. */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (ctx === undefined) {
    throw new Error('useAuth must be used inside <AuthProvider>')
  }
  return ctx
}

// Re-export for callers that want to type-narrow on the kick error.
export { UnauthorizedError }
