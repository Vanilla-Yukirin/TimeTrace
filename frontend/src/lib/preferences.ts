import { useCallback, useSyncExternalStore } from 'react'

/** Per-browser UI preferences (localStorage). Every access is guarded: storage
 *  can throw or be empty (private mode, blocked site data), and the UI must
 *  render with defaults either way. */

const SAKURA_KEY = 'tt_sakura'
const CHANGE_EVENT = 'tt-preferences-change'

function readSakura(): boolean {
  try {
    return localStorage.getItem(SAKURA_KEY) === '1'
  } catch {
    return false
  }
}

function subscribe(onChange: () => void): () => void {
  const onStorage = (e: StorageEvent) => {
    if (e.key === SAKURA_KEY) onChange()
  }
  window.addEventListener('storage', onStorage)
  window.addEventListener(CHANGE_EVENT, onChange)
  return () => {
    window.removeEventListener('storage', onStorage)
    window.removeEventListener(CHANGE_EVENT, onChange)
  }
}

/** Decorative sakura animation — off by default (it keeps the GPU busy). */
export function useSakuraEnabled(): [boolean, (on: boolean) => void] {
  const enabled = useSyncExternalStore(subscribe, readSakura, () => false)
  const setEnabled = useCallback((on: boolean) => {
    try {
      localStorage.setItem(SAKURA_KEY, on ? '1' : '0')
    } catch {
      /* storage unavailable — the toggle just won't persist */
    }
    window.dispatchEvent(new Event(CHANGE_EVENT))
  }, [])
  return [enabled, setEnabled]
}
