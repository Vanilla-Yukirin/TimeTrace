import { Moon, Sun } from 'lucide-react'
import { useTheme } from '@/contexts/theme'

/** Compact sun/moon button that flips the active theme. */
export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme()
  const isDark = theme === 'dark'
  return (
    <button
      type="button"
      onClick={toggleTheme}
      title={isDark ? '切换到浅色模式' : '切换到深色模式'}
      aria-label={isDark ? '切换到浅色模式' : '切换到深色模式'}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 34,
        height: 34,
        borderRadius: 'var(--radius-md)',
        background: 'var(--bg-raised)',
        border: '1px solid var(--bg-border)',
        color: 'var(--text-secondary)',
        cursor: 'pointer',
      }}
    >
      {isDark ? <Moon size={16} /> : <Sun size={16} />}
    </button>
  )
}
