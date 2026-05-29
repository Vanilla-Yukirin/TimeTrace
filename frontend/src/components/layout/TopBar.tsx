import { useNavigate } from 'react-router-dom'
import { LogOut } from 'lucide-react'
import { api } from '@/lib/api'
import { useAuth } from '@/contexts/AuthContext'

export function TopBar() {
  const { user, refresh } = useAuth()
  const navigate = useNavigate()

  async function onLogout() {
    try {
      await api.logout()
    } catch {
      // Even if logout fails (network / already 401), force the local-state
      // clear by refreshing /me (which will 401 → user=null → bounce).
    }
    refresh()
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
