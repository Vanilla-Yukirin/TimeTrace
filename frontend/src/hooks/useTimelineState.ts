import { useState, useCallback } from 'react'

export interface TimelineViewport {
  offsetMs: number    // epoch ms at left edge
  scaleMs: number     // ms per pixel (lower = more zoomed in)
  canvasWidth: number // logical canvas width in px
}

const MIN_SCALE = 100       // 1px = 100ms (max zoom-in: ~1.7min/screen)
const MAX_SCALE = 86_400    // 1px = 86.4s (full day per screen)

function clamp(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, v))
}

export function useTimelineState(date: Date) {
  const dayStartMs = (() => {
    const d = new Date(date)
    d.setHours(0, 0, 0, 0)
    return d.getTime()
  })()

  const [viewport, setViewport] = useState<TimelineViewport>({
    offsetMs: dayStartMs,
    scaleMs: MAX_SCALE,   // will be recalculated once canvas width is known
    canvasWidth: 800,
  })

  const initViewport = useCallback((canvasWidth: number) => {
    setViewport({
      offsetMs: dayStartMs,
      scaleMs: 86_400_000 / canvasWidth,
      canvasWidth,
    })
  }, [dayStartMs])

  const zoom = useCallback((factor: number, mouseX: number) => {
    setViewport(v => {
      const anchorTs = mouseX * v.scaleMs + v.offsetMs
      const newScale = clamp(v.scaleMs * factor, MIN_SCALE, MAX_SCALE)
      return {
        ...v,
        scaleMs: newScale,
        offsetMs: anchorTs - mouseX * newScale,
      }
    })
  }, [])

  const pan = useCallback((deltaX: number) => {
    setViewport(v => ({
      ...v,
      offsetMs: v.offsetMs - deltaX * v.scaleMs,
    }))
  }, [])

  const goToday = useCallback((canvasWidth: number) => {
    setViewport({
      offsetMs: dayStartMs,
      scaleMs: 86_400_000 / canvasWidth,
      canvasWidth,
    })
  }, [dayStartMs])

  return { viewport, setViewport, initViewport, zoom, pan, goToday }
}
