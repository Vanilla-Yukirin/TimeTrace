import { Link, useLocation } from 'react-router-dom'
import { Clock, Search, Settings, Sparkles } from 'lucide-react'
import { Logo } from '@/components/brand/Logo'
import { CatMascot } from '@/components/brand/CatMascot'

const NAV = [
  { to: '/', icon: Clock, label: '时间轴' },
  { to: '/search', icon: Search, label: '搜索' },
  { to: '/settings', icon: Settings, label: '设置' },
]

export function Sidebar() {
  const { pathname } = useLocation()

  return (
    <aside
      className="flex flex-col h-full shrink-0"
      style={{
        width: 216,
        borderRight: '1px solid var(--bg-border)',
        background: 'var(--bg-surface)',
      }}
    >
      {/* Brand */}
      <div style={{ padding: '18px 16px 14px' }}>
        <Logo size={32} withWordmark tagline="记录时间 · 追踪生活" />
      </div>

      {/* Nav */}
      <nav style={{ padding: '4px 10px', display: 'flex', flexDirection: 'column', gap: 3 }}>
        {NAV.map(({ to, icon: Icon, label }) => {
          const active = pathname === to
          return (
            <Link
              key={to}
              to={to}
              className="relative flex items-center gap-2.5 transition-colors"
              style={{
                padding: '9px 12px',
                borderRadius: 'var(--radius-md)',
                fontSize: 13.5,
                fontWeight: active ? 600 : 500,
                color: active ? 'var(--accent)' : 'var(--text-secondary)',
                background: active ? 'var(--accent-subtle)' : 'transparent',
                textDecoration: 'none',
              }}
            >
              {/* active rail */}
              {active && (
                <span
                  style={{
                    position: 'absolute',
                    left: -10,
                    top: '50%',
                    transform: 'translateY(-50%)',
                    width: 3,
                    height: 18,
                    borderRadius: 'var(--radius-pill)',
                    background: 'var(--grad-brand)',
                  }}
                />
              )}
              <Icon size={16} />
              {label}
            </Link>
          )
        })}
      </nav>

      <div style={{ flex: 1 }} />

      {/* Mascot foot — pure atmosphere */}
      <div
        style={{
          position: 'relative',
          margin: 12,
          padding: '20px 12px 16px',
          borderRadius: 'var(--radius-lg)',
          background: 'var(--grad-brand-soft)',
          border: '1px solid var(--bg-border)',
          overflow: 'hidden',
          textAlign: 'center',
        }}
      >
        <Sparkles
          size={13}
          style={{ position: 'absolute', top: 12, right: 16, color: 'var(--accent)', opacity: 0.7 }}
        />
        <Sparkles
          size={9}
          style={{ position: 'absolute', top: 30, left: 18, color: 'var(--accent)', opacity: 0.5 }}
        />
        <CatMascot size={88} float style={{ display: 'block', margin: '0 auto' }} />
        <div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--text-secondary)', fontWeight: 500 }}>
          今天也在好好记录
        </div>
        <div style={{ marginTop: 2, fontSize: 10, color: 'var(--text-muted)' }}>
          本地优先 · 隐私自持
        </div>
      </div>
    </aside>
  )
}
