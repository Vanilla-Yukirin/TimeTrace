import { useEffect, useState } from 'react'

/** Narrow-viewport breakpoint shared across the app. Inline styles can't carry
 *  `@media`, so layout components branch on this hook instead. */
export const MOBILE_BREAKPOINT = 768
const QUERY = `(max-width: ${MOBILE_BREAKPOINT}px)`

export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== 'undefined' && 'matchMedia' in window
      ? window.matchMedia(QUERY).matches
      : false,
  )

  useEffect(() => {
    const mq = window.matchMedia(QUERY)
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches)
    mq.addEventListener('change', handler)
    setIsMobile(mq.matches) // re-sync in case it changed before the listener attached
    return () => mq.removeEventListener('change', handler)
  }, [])

  return isMobile
}
