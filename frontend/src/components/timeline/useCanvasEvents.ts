import { useRef, useCallback } from 'react'
import type { ApiRecord } from '@/types/api'
import type { TimelineViewport } from '@/hooks/useTimelineState'
import { hitTest } from './useCanvasRenderer'

interface UseCanvasEventsOptions {
  records: ApiRecord[]
  viewport: TimelineViewport
  zoom: (factor: number, mouseX: number) => void
  pan: (deltaX: number) => void
  onHover: (recordId: string | null) => void
  onClick: (record: ApiRecord | null) => void
}

export function useCanvasEvents({
  records, viewport, zoom, pan, onHover, onClick,
}: UseCanvasEventsOptions) {
  const dragging = useRef(false)
  const lastX = useRef(0)
  const moved = useRef(false)

  const onWheel = useCallback((e: WheelEvent) => {
    e.preventDefault()
    const factor = e.deltaY > 0 ? 1.25 : 0.8
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
    zoom(factor, e.clientX - rect.left)
  }, [zoom])

  const onMouseDown = useCallback((e: MouseEvent) => {
    dragging.current = true
    lastX.current = e.clientX
    moved.current = false
  }, [])

  const onMouseMove = useCallback((e: MouseEvent) => {
    const el = e.target as HTMLElement
    const rect = el.getBoundingClientRect()
    const mouseX = e.clientX - rect.left
    const mouseY = e.clientY - rect.top

    if (dragging.current) {
      const delta = e.clientX - lastX.current
      if (Math.abs(delta) > 2) moved.current = true
      pan(delta)
      lastX.current = e.clientX
      onHover(null)
    } else {
      const hit = hitTest(mouseX, mouseY, records, viewport)
      onHover(hit?.record.id ?? null)
    }
  }, [records, viewport, pan, onHover])

  const onMouseUp = useCallback((e: MouseEvent) => {
    if (!moved.current) {
      const el = e.target as HTMLElement
      const rect = el.getBoundingClientRect()
      const hit = hitTest(e.clientX - rect.left, e.clientY - rect.top, records, viewport)
      onClick(hit?.record ?? null)
    }
    dragging.current = false
    moved.current = false
  }, [records, viewport, onClick])

  const onMouseLeave = useCallback(() => {
    dragging.current = false
    onHover(null)
  }, [onHover])

  return { onWheel, onMouseDown, onMouseMove, onMouseUp, onMouseLeave }
}
