import { useNavigate } from 'react-router-dom'
import { KeyRound, LogOut } from 'lucide-react'
import { toast } from 'sonner'
import { api, broadcastKick } from '@/lib/api'
import { useAuth } from '@/contexts/AuthContext'

/** Settings → Account: who am I, change password, log out. */
export function AccountSection() {
  const { user, setUser } = useAuth()
  const navigate = useNavigate()

  async function onLogout() {
    try {
      await api.logout()
    } catch {
      // ignore — we clear local state below regardless
    }
    setUser(null) // synchronous clear → no stale-user bounce/flicker
    broadcastKick() // kick other tabs (204 logout doesn't trip the 401 path)
    toast.success('已退出登录')
    navigate('/login', { replace: true })
  }

  return (
    <div>
      <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 12 }}>
        账户
      </h3>
      <div
        style={{
          padding: 16,
          background: 'var(--bg-surface)',
          borderRadius: 8,
          border: '1px solid var(--bg-border)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>用户名</div>
          <div style={{ fontSize: 14, color: 'var(--text-primary)', fontWeight: 600 }}>
            {user?.username ?? '—'}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            onClick={() => navigate('/login/change-password')}
            style={btnStyle('var(--text-secondary)')}
          >
            <KeyRound size={14} />
            修改密码
          </button>
          <button onClick={onLogout} style={btnStyle('#ef4444')}>
            <LogOut size={14} />
            退出登录
          </button>
        </div>
      </div>
    </div>
  )
}

function btnStyle(color: string): React.CSSProperties {
  return {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    padding: '6px 12px',
    background: 'transparent',
    color,
    border: '1px solid var(--bg-border)',
    borderRadius: 6,
    fontSize: 13,
    cursor: 'pointer',
  }
}
