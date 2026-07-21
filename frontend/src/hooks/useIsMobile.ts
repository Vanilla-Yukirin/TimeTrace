import { useSyncExternalStore } from 'react'

/** Narrow-viewport breakpoint shared across the app. Inline styles can't carry
 *  `@media`, so layout components branch on this hook instead. */
export const MOBILE_BREAKPOINT = 768
const QUERY = `(max-width: ${MOBILE_BREAKPOINT}px)`

function subscribe(onStoreChange: () => void): () => void {
  const query = window.matchMedia(QUERY)
  query.addEventListener('change', onStoreChange)
  return () => query.removeEventListener('change', onStoreChange)
}

function getSnapshot(): boolean {
  return window.matchMedia(QUERY).matches
}

export function useIsMobile(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, () => false)
}
