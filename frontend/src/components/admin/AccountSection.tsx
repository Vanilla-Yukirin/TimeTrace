import { useNavigate } from 'react-router-dom'
import { KeyRound, LogOut } from 'lucide-react'
import { toast } from 'sonner'
import { api, broadcastKick } from '@/lib/api'
import { useAuth } from '@/contexts/auth'
import { Card, Section } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'

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
    <Section title="账户">
      <Card
        style={{
          padding: 16,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: 10,
        }}
      >
        <div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>用户名</div>
          <div style={{ fontSize: 14, color: 'var(--text-primary)', fontWeight: 600 }}>
            {user?.username ?? '—'}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <Button onClick={() => navigate('/login/change-password')}>
            <KeyRound size={14} aria-hidden="true" />
            修改密码
          </Button>
          <Button variant="danger" onClick={onLogout}>
            <LogOut size={14} aria-hidden="true" />
            退出登录
          </Button>
        </div>
      </Card>
    </Section>
  )
}
