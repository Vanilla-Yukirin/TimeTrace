import { Moon, Sun } from 'lucide-react'
import { useTheme } from '@/contexts/theme'
import { IconButton } from './IconButton'

/** Compact sun/moon button that flips the active theme. */
export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme()
  const isDark = theme === 'dark'
  return (
    <IconButton
      onClick={toggleTheme}
      title={isDark ? '切换到浅色模式' : '切换到深色模式'}
      aria-label={isDark ? '切换到浅色模式' : '切换到深色模式'}
      style={{ background: 'var(--bg-raised)' }}
    >
      {isDark ? <Moon size={16} /> : <Sun size={16} />}
    </IconButton>
  )
}
