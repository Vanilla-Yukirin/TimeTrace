import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { ThemeContext, type Theme, type ThemeContextValue } from './theme'

const STORAGE_KEY = 'tt_theme'

/** Read the theme the inline bootstrap script (index.html) already applied to
 *  <html>, so React's first render matches the painted DOM (no flash). Falls
 *  back to the stored value or 'dark'. */
function readInitialTheme(): Theme {
  if (typeof document !== 'undefined') {
    const attr = document.documentElement.getAttribute('data-theme')
    if (attr === 'light' || attr === 'dark') return attr
  }
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    /* localStorage unavailable */
  }
  return 'dark'
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(readInitialTheme)

  // Reflect the theme onto <html> + persist. The bootstrap script handles the
  // very first paint; this keeps the attribute in sync on every later change.
  // The color cross-fade is only enabled for the moment of a flip (via the
  // html.theme-transition class) — a permanent `* { transition }` made every
  // layout/hover change pay for transitions on every element.
  const firstThemeRef = useRef(true)
  useEffect(() => {
    const root = document.documentElement
    let timer: number | undefined
    if (firstThemeRef.current) {
      firstThemeRef.current = false
    } else {
      root.classList.add('theme-transition')
      timer = window.setTimeout(() => root.classList.remove('theme-transition'), 320)
    }
    root.setAttribute('data-theme', theme)
    try {
      localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      /* ignore */
    }
    return () => {
      if (timer !== undefined) {
        window.clearTimeout(timer)
        root.classList.remove('theme-transition')
      }
    }
  }, [theme])

  // Cross-tab sync: if another tab flips the theme, follow it.
  useEffect(() => {
    const handler = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY && (e.newValue === 'light' || e.newValue === 'dark')) {
        setThemeState(e.newValue)
      }
    }
    window.addEventListener('storage', handler)
    return () => window.removeEventListener('storage', handler)
  }, [])

  const setTheme = useCallback((t: Theme) => setThemeState(t), [])
  const toggleTheme = useCallback(
    () => setThemeState((prev) => (prev === 'dark' ? 'light' : 'dark')),
    [],
  )

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, toggleTheme, setTheme }),
    [theme, toggleTheme, setTheme],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}
