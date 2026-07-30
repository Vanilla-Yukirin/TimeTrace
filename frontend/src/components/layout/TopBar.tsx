import { useNavigate, useLocation, Link } from 'react-router-dom'
import { LogOut, Menu, Search } from 'lucide-react'
import { toast } from 'sonner'
import { api, broadcastKick } from '@/lib/api'
import { useAuth } from '@/contexts/auth'
import { ThemeToggle } from '@/components/ui/ThemeToggle'
import { IconButton } from '@/components/ui/IconButton'
import { Divider } from '@/components/ui/PageShell'
import { navTitleFor } from './nav'

export function TopBar({ isMobile = false, onMenu }: { isMobile?: boolean; onMenu?: () => void }) {
  const { user, setUser } = useAuth()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const title = navTitleFor(pathname)
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
      {/* Left: hamburger (mobile) + current page */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 9, minWidth: 0 }}>
        {isMobile && onMenu && (
          <button
            onClick={onMenu}
            aria-label="打开菜单"
            className="tt-nav-row"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 38,
              height: 38,
              marginLeft: -6,
              borderRadius: 'var(--radius-md)',
              background: 'transparent',
              border: 'none',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
            }}
          >
            <Menu size={20} />
          </button>
        )}
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
            flexShrink: 0,
          }}
        >
          <TitleIcon size={15} />
        </span>
        <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', whiteSpace: 'nowrap' }}>
          {title.label}
        </span>
      </div>

      {/* Right: global actions */}
      {user && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Link
            to="/search"
            title="搜索活动"
            aria-label="搜索活动"
            className="tt-nav-row"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 7,
              padding: isMobile ? 0 : '0 12px',
              width: isMobile ? 34 : undefined,
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
            {!isMobile && <span>搜索…</span>}
          </Link>

          <ThemeToggle />

          <Divider vertical style={{ height: 22, margin: '0 2px' }} />

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
                color: 'var(--accent-contrast)',
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

          <IconButton
            onClick={onLogout}
            title="退出登录"
            aria-label="退出登录"
            style={{ background: 'var(--bg-raised)' }}
          >
            <LogOut size={15} />
          </IconButton>
        </div>
      )}
    </header>
  )
}
