import { useSyncExternalStore } from 'react'

/** Narrow-viewport breakpoint shared across the app. Inline styles can't carry
 *  `@media`, so layout components branch on this hook instead. */
export const MOBILE_BREAKPOINT = 768
export const MOBILE_QUERY = `(max-width: ${MOBILE_BREAKPOINT}px)`

export function getMobileMediaQuery(): MediaQueryList | null {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null
  return window.matchMedia(MOBILE_QUERY)
}

function subscribe(onStoreChange: () => void): () => void {
  const query = getMobileMediaQuery()
  if (!query) return () => undefined
  query.addEventListener('change', onStoreChange)
  return () => query.removeEventListener('change', onStoreChange)
}

function getSnapshot(): boolean {
  return getMobileMediaQuery()?.matches ?? false
}

export function useIsMobile(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, () => false)
}
