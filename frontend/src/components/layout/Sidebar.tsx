import { Link, useLocation } from 'react-router-dom'
import { Clock, Search, Settings } from 'lucide-react'

export function Sidebar() {
  const { pathname } = useLocation()

  return (
    <aside
      style={{ width: 240, borderRight: '1px solid var(--bg-border)', background: 'var(--bg-surface)' }}
      className="flex flex-col h-full shrink-0"
    >
      <div style={{ borderTop: '1px solid var(--bg-border)' }} className="py-2">
        <div className="px-3 py-2" style={{ color: 'var(--text-primary)', fontWeight: 600, fontSize: 14 }}>
          TimeTrace
        </div>
        {NAV.map(({ to, icon: Icon, label }) => (
          <Link
            key={to}
            to={to}
            className="flex items-center gap-2 px-3 py-2 mx-2 rounded-md text-sm transition-colors"
            style={{
              color: pathname === to ? 'var(--accent-hover)' : 'var(--text-secondary)',
              background: pathname === to ? 'var(--accent-subtle)' : 'transparent',
              textDecoration: 'none',
            }}
          >
            <Icon size={15} />
            {label}
          </Link>
        ))}
      </div>
    </aside>
  )
}

const NAV = [
  { to: '/', icon: Clock, label: '时间轴' },
  { to: '/search', icon: Search, label: '搜索' },
  { to: '/settings', icon: Settings, label: '设置' },
]
