import { useNavigate } from 'react-router-dom'
import { LogOut } from 'lucide-react'
import { toast } from 'sonner'
import { api, broadcastKick } from '@/lib/api'
import { useAuth } from '@/contexts/AuthContext'

export function TopBar() {
  const { user, setUser } = useAuth()
  const navigate = useNavigate()

  async function onLogout() {
    try {
      await api.logout()
    } catch {
      // Even if the server call fails (network / already-expired), we still
      // clear local state below so the user isn't stuck "logged in".
    }
    // Synchronously clear the cached principal → user becomes null this render,
    // so /login renders its form immediately instead of bouncing back into the
    // app off a stale cached user ("退出闪烁" bug). Don't rely on an async /me
    // refetch here — that's what caused the flicker.
    setUser(null)
    // A 204 logout doesn't trip apiFetch's 401 broadcast, so kick other tabs
    // explicitly — otherwise they'd keep showing authed UI against the now-
    // revoked cookie until their next window-focus.
    broadcastKick()
    toast.success('已退出登录')
    navigate('/login', { replace: true })
  }

  return (
    <header
      className="flex items-center justify-between px-4 shrink-0"
      style={{
        height: 56,
        borderBottom: '1px solid var(--bg-border)',
        background: 'var(--bg-surface)',
      }}
    >
      <span
        className="font-semibold text-base"
        style={{ color: 'var(--text-primary)', letterSpacing: '0.02em' }}
      >
        TimeTrace
      </span>
      {user && (
        <div className="flex items-center gap-3">
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{user.username}</span>
          <button
            onClick={onLogout}
            title="退出登录"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '6px 10px',
              background: 'transparent',
              color: 'var(--text-secondary)',
              border: '1px solid var(--bg-border)',
              borderRadius: 6,
              fontSize: 12,
              cursor: 'pointer',
            }}
          >
            <LogOut size={14} />
            退出
          </button>
        </div>
      )}
    </header>
  )
}
