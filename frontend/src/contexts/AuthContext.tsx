import {
  useCallback,
  useEffect,
  useMemo,
  type ReactNode,
} from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import type { AuthMe } from '@/types/api'
import { AuthContext, type AuthContextValue } from './auth'

/** Wraps the app, polls /v1/auth/me, and listens for cross-tab 401 kicks. */
export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()

  // The "am I logged in?" probe.
  // - retry: false → a 401 shouldn't be retried; it's a definitive "logged out".
  // - refetchOnWindowFocus → catch server-side session expiry when the user
  //   comes back to the tab.
  const meQuery = useQuery({
    queryKey: queryKeys.authMe(),
    queryFn: api.me,
    retry: false,
    refetchOnWindowFocus: true,
    staleTime: 30_000,
  })

  // CRITICAL: react-query keeps the last successful `data` even after a later
  // fetch ERRORS (e.g. the post-logout /me that now 401s). So deriving user
  // from `data` alone leaves a stale truthy user after logout → the login page
  // bounces back into the app ("闪烁"). Treat an errored probe as logged-out.
  const user: AuthMe | null = meQuery.isError ? null : (meQuery.data ?? null)

  const refetch = useCallback(async (): Promise<AuthMe | null> => {
    // refetchQueries resolves after the query settles; a successful refetch
    // also CLEARS any prior error status, so a login right after a 401 probe
    // correctly flips user from null → the new principal.
    await queryClient.refetchQueries({ queryKey: queryKeys.authMe() })
    const data = queryClient.getQueryData<AuthMe>(queryKeys.authMe())
    const state = queryClient.getQueryState(queryKeys.authMe())
    return state?.status === 'error' ? null : (data ?? null)
  }, [queryClient])

  const setUser = useCallback(
    (me: AuthMe | null) => {
      // setQueryData both updates data AND resets the query to a success
      // status — so a previously-errored probe no longer forces user=null.
      queryClient.setQueryData(queryKeys.authMe(), me)
    },
    [queryClient],
  )

  // Cross-tab logout: another tab caught a 401 and broadcast via
  // localStorage['tt_auth_kicked']. Clear our own auth state so RequireAuth
  // redirects this tab to /login too.
  useEffect(() => {
    const handler = (e: StorageEvent) => {
      if (e.key === 'tt_auth_kicked') setUser(null)
    }
    window.addEventListener('storage', handler)
    return () => window.removeEventListener('storage', handler)
  }, [setUser])

  const value = useMemo<AuthContextValue>(
    () => ({ user, loading: meQuery.isLoading, refetch, setUser }),
    [user, meQuery.isLoading, refetch, setUser],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
