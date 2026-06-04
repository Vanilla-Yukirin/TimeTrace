import * as Dialog from '@radix-ui/react-dialog'
import { Link, useLocation } from 'react-router-dom'
import { Clock, Search, Settings, Sparkles, MessageCircle, LayoutDashboard, ScrollText } from 'lucide-react'
import { Logo } from '@/components/brand/Logo'
import { CatMascot } from '@/components/brand/CatMascot'

const NAV = [
  { to: '/', icon: Clock, label: '时间轴' },
  { to: '/agent', icon: MessageCircle, label: '问问' },
  { to: '/dashboard', icon: LayoutDashboard, label: '看板' },
  { to: '/audit', icon: ScrollText, label: '日志' },
  { to: '/search', icon: Search, label: '搜索' },
  { to: '/settings', icon: Settings, label: '设置' },
]

interface SidebarProps {
  isMobile?: boolean
  open?: boolean
  onClose?: () => void
}

const srOnly: React.CSSProperties = {
  position: 'absolute',
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: 'hidden',
  clip: 'rect(0, 0, 0, 0)',
  whiteSpace: 'nowrap',
  border: 0,
}

export function Sidebar({ isMobile = false, open = false, onClose }: SidebarProps) {
  const { pathname } = useLocation()

  const content = (
    <>
      <div style={{ padding: '18px 16px 14px' }}>
        <Logo size={32} withWordmark tagline="记录时间 · 追踪生活" />
      </div>

      <nav aria-label="主导航" style={{ padding: '4px 10px', display: 'flex', flexDirection: 'column', gap: 3 }}>
        {NAV.map(({ to, icon: Icon, label }) => {
          const active = pathname === to
          return (
            <Link
              key={to}
              to={to}
              aria-current={active ? 'page' : undefined}
              onClick={isMobile ? onClose : undefined}
              className="relative flex items-center gap-2.5 transition-colors"
              style={{
                padding: '11px 12px',
                borderRadius: 'var(--radius-md)',
                fontSize: 13.5,
                fontWeight: active ? 600 : 500,
                color: active ? 'var(--accent)' : 'var(--text-secondary)',
                background: active ? 'var(--accent-subtle)' : 'transparent',
                textDecoration: 'none',
              }}
            >
              {active && (
                <span
                  aria-hidden="true"
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
              <Icon size={16} aria-hidden="true" />
              {label}
            </Link>
          )
        })}
      </nav>

      <div style={{ flex: 1, minHeight: 16 }} />

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
          flexShrink: 0,
        }}
      >
        <Sparkles size={13} aria-hidden="true" style={{ position: 'absolute', top: 12, right: 16, color: 'var(--accent)', opacity: 0.7 }} />
        <Sparkles size={9} aria-hidden="true" style={{ position: 'absolute', top: 30, left: 18, color: 'var(--accent)', opacity: 0.5 }} />
        <CatMascot size={88} float style={{ display: 'block', margin: '0 auto' }} />
        <div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--text-secondary)', fontWeight: 500 }}>
          今天也在好好记录
        </div>
        <div style={{ marginTop: 2, fontSize: 10, color: 'var(--text-muted)' }}>
          本地优先 · 隐私自持
        </div>
      </div>
    </>
  )

  // Desktop: static column.
  if (!isMobile) {
    return (
      <aside
        className="flex flex-col h-full shrink-0"
        style={{ width: 216, borderRight: '1px solid var(--bg-border)', background: 'var(--bg-surface)' }}
      >
        {content}
      </aside>
    )
  }

  // Mobile: Radix Dialog drawer — gives focus trap/restore, background inert,
  // Escape, and body scroll-lock for free (matches ImageLightbox's pattern).
  return (
    <Dialog.Root open={open} onOpenChange={(o) => { if (!o) onClose?.() }}>
      <Dialog.Portal>
        <Dialog.Overlay
          className="tt-overlay"
          style={{ position: 'fixed', inset: 0, background: 'rgba(0, 0, 0, 0.5)', zIndex: 40 }}
        />
        <Dialog.Content
          className="tt-drawer-content flex flex-col"
          aria-label="导航菜单"
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            bottom: 0,
            width: 268,
            maxWidth: '82vw',
            zIndex: 41,
            background: 'var(--bg-surface)',
            borderRight: '1px solid var(--bg-border)',
            boxShadow: 'var(--shadow-lg)',
            overflowY: 'auto',
            outline: 'none',
          }}
        >
          <Dialog.Title style={srOnly}>导航菜单</Dialog.Title>
          {content}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
