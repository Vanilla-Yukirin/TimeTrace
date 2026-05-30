import { useState, type FormEvent } from 'react'
import { useNavigate, useLocation, Navigate } from 'react-router-dom'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { useAuth } from '@/contexts/AuthContext'
import { AuthCard, AuthField, AuthButton } from '@/components/auth/AuthCard'

/** Where to bounce back after login — RequireAuth stashes the original
 *  pathname into ``location.state.from`` when it redirects to /login. */
interface LocationState {
  from?: string
}

export function LoginPage() {
  const { user, refetch } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const rawFrom = (location.state as LocationState | null)?.from ?? '/'
  // Never bounce a freshly-logged-in user back onto an auth route. If they hit
  // /login/change-password while unauthenticated, RequireAuth stashes that as
  // `from`; landing a normal (must_change=false) user there post-login shows
  // the change-password form unexpectedly. Normalize any /login* → '/'.
  const from = rawFrom.startsWith('/login') ? '/' : rawFrom

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // Already logged in (someone hit /login by mistake / browser back) → bounce.
  if (user) {
    return (
      <Navigate to={user.must_change_password ? '/login/change-password' : from} replace />
    )
  }

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const resp = await api.login({ username, password })
      // MUST await: refetch /me with the fresh cookie so AuthContext.user is
      // populated (and any prior 401-error status cleared) BEFORE we navigate.
      // Navigating first would let RequireAuth read stale/empty auth state and
      // bounce us straight back to /login.
      await refetch()
      navigate(resp.must_change_password ? '/login/change-password' : from, { replace: true })
    } catch (err) {
      // The server returns the same 401 detail for "wrong user" and "wrong
      // password" — we surface its text verbatim so rate-limit (429) messages
      // come through too.
      const msg = (err as Error).message
      setError(msg)
      toast.error('登录失败', { description: msg })
      setSubmitting(false)
    }
    // NOTE: on success we intentionally do NOT clear `submitting` — the page is
    // navigating away; leaving the button disabled avoids a double-submit flash.
  }

  return (
    <AuthCard
      title="TimeTrace"
      subtitle="登录后继续 · 记录时间 · 追踪生活"
      footer="首次登录默认 admin / admin"
    >
      <form onSubmit={onSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <AuthField
          label="用户名"
          name="username"
          value={username}
          onChange={setUsername}
          autoComplete="username"
          autoFocus
        />
        <AuthField
          label="密码"
          name="password"
          type="password"
          value={password}
          onChange={setPassword}
          autoComplete="current-password"
        />

        {error && <div style={{ fontSize: 12, color: 'var(--error)' }}>{error}</div>}

        <AuthButton disabled={submitting || !username || !password} busy={submitting}>
          {submitting ? '登录中…' : '登录'}
        </AuthButton>
      </form>
    </AuthCard>
  )
}
