import { useNavigate, useLocation, Link } from 'react-router-dom'
import { Clock, LogOut, Search, Settings } from 'lucide-react'
import { toast } from 'sonner'
import { api, broadcastKick } from '@/lib/api'
import { useAuth } from '@/contexts/AuthContext'
import { ThemeToggle } from '@/components/ui/ThemeToggle'

/** Per-route title shown on the left of the bar. */
const TITLES: Record<string, { icon: typeof Clock; label: string }> = {
  '/': { icon: Clock, label: '时间轴' },
  '/search': { icon: Search, label: '搜索' },
  '/settings': { icon: Settings, label: '设置' },
}

export function TopBar() {
  const { user, setUser } = useAuth()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const title = TITLES[pathname] ?? TITLES['/']
  const TitleIcon = title.icon

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

  const initial = (user?.username?.[0] ?? '?').toUpperCase()

  return (
    <header
      className="flex items-center justify-between shrink-0"
      style={{
        height: 58,
        padding: '0 18px',
        borderBottom: '1px solid var(--bg-border)',
        background: 'color-mix(in srgb, var(--bg-surface) 82%, transparent)',
        backdropFilter: 'blur(10px)',
      }}
    >
      {/* Left: current page */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 28,
            height: 28,
            borderRadius: 'var(--radius-md)',
            background: 'var(--accent-subtle)',
            color: 'var(--accent)',
          }}
        >
          <TitleIcon size={15} />
        </span>
        <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>
          {title.label}
        </span>
      </div>

      {/* Right: global actions */}
      {user && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Link
            to="/search"
            title="搜索活动"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 7,
              padding: '0 12px',
              height: 34,
              borderRadius: 'var(--radius-md)',
              background: 'var(--bg-raised)',
              border: '1px solid var(--bg-border)',
              color: 'var(--text-muted)',
              fontSize: 13,
              textDecoration: 'none',
            }}
          >
            <Search size={14} />
            <span>搜索…</span>
          </Link>

          <ThemeToggle />

          <div
            style={{
              width: 1,
              height: 22,
              background: 'var(--bg-border)',
              margin: '0 2px',
            }}
          />

          {/* user chip */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 30,
                height: 30,
                borderRadius: 'var(--radius-pill)',
                background: 'var(--grad-accent)',
                color: '#fff',
                fontSize: 13,
                fontWeight: 700,
                boxShadow: 'var(--shadow-sm)',
              }}
            >
              {initial}
            </span>
            <span
              style={{ fontSize: 13, color: 'var(--text-secondary)', fontWeight: 500 }}
              className="hidden sm:inline"
            >
              {user.username}
            </span>
          </div>

          <button
            onClick={onLogout}
            title="退出登录"
            aria-label="退出登录"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 34,
              height: 34,
              borderRadius: 'var(--radius-md)',
              background: 'var(--bg-raised)',
              color: 'var(--text-secondary)',
              border: '1px solid var(--bg-border)',
              cursor: 'pointer',
            }}
          >
            <LogOut size={15} />
          </button>
        </div>
      )}
    </header>
  )
}
