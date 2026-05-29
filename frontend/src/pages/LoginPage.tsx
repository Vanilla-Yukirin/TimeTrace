import { useState, type FormEvent } from 'react'
import { useNavigate, useLocation, Navigate } from 'react-router-dom'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { useAuth } from '@/contexts/AuthContext'

/** Where to bounce back after login — RequireAuth stashes the original
 *  pathname into ``location.state.from`` when it redirects to /login. */
interface LocationState {
  from?: string
}

export function LoginPage() {
  const { user, refetch } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as LocationState | null)?.from ?? '/'

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
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--bg-app)',
        padding: 24,
      }}
    >
      <form
        onSubmit={onSubmit}
        style={{
          width: '100%',
          maxWidth: 360,
          padding: 32,
          background: 'var(--bg-surface)',
          border: '1px solid var(--bg-border)',
          borderRadius: 12,
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
        }}
      >
        <div style={{ marginBottom: 8 }}>
          <h1 style={{ fontSize: 20, fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
            TimeTrace
          </h1>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
            登录后继续
          </div>
        </div>

        <Field
          label="用户名"
          name="username"
          value={username}
          onChange={setUsername}
          autoComplete="username"
          autoFocus
        />
        <Field
          label="密码"
          name="password"
          type="password"
          value={password}
          onChange={setPassword}
          autoComplete="current-password"
        />

        {error && (
          <div style={{ fontSize: 12, color: '#ef4444' }}>{error}</div>
        )}

        <button
          type="submit"
          disabled={submitting || !username || !password}
          style={{
            padding: '10px 16px',
            background: submitting ? 'var(--bg-raised)' : '#2563eb',
            color: '#fff',
            border: 'none',
            borderRadius: 6,
            fontSize: 14,
            fontWeight: 500,
            cursor: submitting ? 'wait' : 'pointer',
            opacity: !username || !password ? 0.6 : 1,
          }}
        >
          {submitting ? '登录中...' : '登录'}
        </button>

        <div
          style={{
            fontSize: 11,
            color: 'var(--text-muted)',
            textAlign: 'center',
            marginTop: 4,
          }}
        >
          首次登录默认 admin / admin
        </div>
      </form>
    </div>
  )
}

interface FieldProps {
  label: string
  name: string
  value: string
  onChange: (v: string) => void
  type?: string
  autoComplete?: string
  autoFocus?: boolean
}

function Field({ label, name, value, onChange, type = 'text', autoComplete, autoFocus }: FieldProps) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{label}</span>
      <input
        name={name}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete={autoComplete}
        autoFocus={autoFocus}
        style={{
          padding: '8px 12px',
          background: 'var(--bg-raised)',
          border: '1px solid var(--bg-border)',
          borderRadius: 6,
          color: 'var(--text-primary)',
          fontSize: 14,
          outline: 'none',
        }}
      />
    </label>
  )
}
