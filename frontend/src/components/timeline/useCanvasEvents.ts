import { useRef, useCallback, useEffect } from 'react'
import type { ApiRecord } from '@/types/api'
import type { TimelineViewport } from '@/hooks/useTimelineState'
import { hitTest, type SortedRecords } from './useCanvasRenderer'

interface UseCanvasEventsOptions {
  sorted: SortedRecords
  viewport: TimelineViewport
  zoom: (factor: number, mouseX: number) => void
  pan: (deltaX: number) => void
  onHover: (recordId: string | null) => void
  onClick: (record: ApiRecord | null) => void
  /** Called on every pointer move with client coordinates (tooltip follow). */
  onPointer?: (clientX: number, clientY: number) => void
}

// Wheel deltas arrive in pixels (trackpads: many small events) or lines
// (mouse wheels on some platforms); normalise to pixels.
const LINE_PX = 16
const ZOOM_PER_PX = 0.0015

/** Pointer/wheel handling for the timeline canvas. Everything that changes
 *  React state (zoom, pan, hover) is coalesced to at most once per animation
 *  frame — a trackpad fires dozens of wheel events per gesture and mousemove
 *  fires faster than the display refreshes. */
export function useCanvasEvents({
  sorted, viewport, zoom, pan, onHover, onClick, onPointer,
}: UseCanvasEventsOptions) {
  const dragging = useRef(false)
  const lastX = useRef(0)
  const moved = useRef(false)

  // Latest values for the rAF flush (which runs outside React's render).
  const sortedRef = useRef(sorted)
  const viewportRef = useRef(viewport)
  useEffect(() => {
    sortedRef.current = sorted
    viewportRef.current = viewport
  }, [sorted, viewport])

  const pending = useRef({ zoom: 1, zoomAnchor: 0, pan: 0, hover: null as null | { x: number; y: number } })
  const frame = useRef<number | null>(null)

  const flush = useCallback(() => {
    frame.current = null
    const p = pending.current
    if (p.zoom !== 1) {
      zoom(p.zoom, p.zoomAnchor)
      p.zoom = 1
    }
    if (p.pan !== 0) {
      pan(p.pan)
      p.pan = 0
    }
    if (p.hover) {
      const hit = hitTest(p.hover.x, p.hover.y, sortedRef.current, viewportRef.current)
      onHover(hit?.record.id ?? null)
      p.hover = null
    }
  }, [zoom, pan, onHover])

  const schedule = useCallback(() => {
    if (frame.current == null) frame.current = requestAnimationFrame(flush)
  }, [flush])

  useEffect(() => () => {
    if (frame.current != null) cancelAnimationFrame(frame.current)
  }, [])

  const onWheel = useCallback((e: WheelEvent) => {
    e.preventDefault()
    const unit = e.deltaMode === 1 ? LINE_PX : 1
    const dx = e.deltaX * unit
    const dy = e.deltaY * unit
    const p = pending.current
    // Horizontal intent (trackpad swipe, or shift+wheel) pans; vertical zooms
    // (trackpad pinch arrives as ctrl+wheel with deltaY, so it zooms too).
    if (!e.ctrlKey && (Math.abs(dx) > Math.abs(dy) || e.shiftKey)) {
      p.pan -= dx !== 0 ? dx : dy
    } else {
      const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
      const factor = Math.exp(Math.max(-200, Math.min(200, dy)) * ZOOM_PER_PX)
      p.zoom *= factor
      p.zoomAnchor = e.clientX - rect.left
    }
    schedule()
  }, [schedule])

  const onMouseDown = useCallback((e: MouseEvent) => {
    dragging.current = true
    lastX.current = e.clientX
    moved.current = false
  }, [])

  const onMouseMove = useCallback((e: MouseEvent) => {
    onPointer?.(e.clientX, e.clientY)
    const rect = (e.target as HTMLElement).getBoundingClientRect()
    const p = pending.current
    if (dragging.current) {
      const delta = e.clientX - lastX.current
      if (Math.abs(delta) > 2) moved.current = true
      p.pan += delta
      lastX.current = e.clientX
      p.hover = null
      onHover(null)
    } else {
      p.hover = { x: e.clientX - rect.left, y: e.clientY - rect.top }
    }
    schedule()
  }, [onHover, onPointer, schedule])

  const onMouseUp = useCallback((e: MouseEvent) => {
    if (!moved.current) {
      const rect = (e.target as HTMLElement).getBoundingClientRect()
      const hit = hitTest(e.clientX - rect.left, e.clientY - rect.top, sortedRef.current, viewportRef.current)
      onClick(hit?.record ?? null)
    }
    dragging.current = false
    moved.current = false
  }, [onClick])

  const onMouseLeave = useCallback(() => {
    dragging.current = false
    pending.current.hover = null
    onHover(null)
  }, [onHover])

  return { onWheel, onMouseDown, onMouseMove, onMouseUp, onMouseLeave }
}
