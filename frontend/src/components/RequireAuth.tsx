import { Navigate, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import { useAuth } from '@/contexts/AuthContext'

interface Props {
  children: ReactNode
  /**
   * If true, this route is allowed to render even when the user has
   * ``must_change_password=true``. Used exclusively by the change-password
   * page itself — otherwise we'd ping-pong redirect into it forever.
   */
  allowMustChange?: boolean
}

/** Route guard. Three outcomes:
 *
 *  1. Loading (first /v1/auth/me probe in flight) → spinner.
 *  2. Not logged in → redirect to /login.
 *  3. Logged in but must_change_password=true → redirect to
 *     /login/change-password, unless ``allowMustChange`` says we ARE that page.
 *  4. Logged in normally → render children.
 */
export function RequireAuth({ children, allowMustChange = false }: Props) {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--text-muted)',
        }}
      >
        加载中...
      </div>
    )
  }

  if (!user) {
    // ``state.from`` lets LoginPage bounce the user back to whatever they
    // were trying to view, after a successful login.
    return <Navigate to="/login" state={{ from: location.pathname }} replace />
  }

  if (user.must_change_password && !allowMustChange) {
    return <Navigate to="/login/change-password" replace />
  }

  return <>{children}</>
}
