import { useEffect, useState } from 'react'

/** Clock for open-ended activity durations; disabled consumers allocate no timer. */
export function useNow(enabled: boolean, intervalMs = 1_000): number | null {
  const [now, setNow] = useState<number | null>(null)

  useEffect(() => {
    if (!enabled) return

    const tick = () => setNow(Date.now())
    const firstTick = window.setTimeout(tick, 0)
    const interval = window.setInterval(tick, intervalMs)
    return () => {
      window.clearTimeout(firstTick)
      window.clearInterval(interval)
    }
  }, [enabled, intervalMs])

  return now
}
