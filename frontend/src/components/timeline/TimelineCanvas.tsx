import { useRef, useEffect, useState, useCallback } from 'react'
import type { ApiRecord } from '@/types/api'
import { useTimelineState } from '@/hooks/useTimelineState'
import { useTheme } from '@/contexts/theme'
import { formatTime } from '@/lib/dateUtils'
import { renderTimeline, hitTest, CANVAS_H, TIMELINE_PALETTES } from './useCanvasRenderer'
import { useCanvasEvents } from './useCanvasEvents'
import { TimelineTooltip } from './TimelineTooltip'

interface TimelineCanvasProps {
  records: ApiRecord[]
  date: Date
  selectedRecordId: string | null
  onSelectRecord: (id: string | null) => void
}

export function TimelineCanvas({
  records, date, selectedRecordId, onSelectRecord,
}: TimelineCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [hoverRecordId, setHoverRecordId] = useState<string | null>(null)
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number } | null>(null)

  const { viewport, setViewport, initViewport, zoom, pan, goToday } = useTimelineState(date)
  const { theme } = useTheme()
  const palette = TIMELINE_PALETTES[theme]

  // Touch gesture state persists across re-renders (the render effect re-runs on
  // every pan/zoom, so this can't live in the touch effect's closure). records /
  // viewport are read via refs so tap-hit-testing always sees current values.
  const touchRef = useRef({ mode: 'none' as 'none' | 'pan' | 'pinch', lastX: 0, lastDist: 0, startX: 0, startY: 0, moved: false })
  const recordsRef = useRef(records)
  const viewportRef = useRef(viewport)

  useEffect(() => {
    recordsRef.current = records
  }, [records])

  useEffect(() => {
    viewportRef.current = viewport
  }, [viewport])

  // Track whether the canvas has been initialized at least once
  const initializedRef = useRef(false)

  // Init on mount and resize
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const obs = new ResizeObserver(entries => {
      const { width } = entries[0].contentRect
      const canvas = canvasRef.current
      if (!canvas) return
      const dpr = window.devicePixelRatio || 1
      canvas.width = width * dpr
      canvas.height = CANVAS_H * dpr
      canvas.style.width = `${width}px`
      canvas.style.height = `${CANVAS_H}px`
      // Do NOT call ctx.scale here — the render effect applies setTransform on every frame
      if (!initializedRef.current) {
        initializedRef.current = true
        initViewport(width)
      } else {
        setViewport(v => ({ ...v, canvasWidth: width }))
      }
    })
    obs.observe(el)
    return () => { obs.disconnect(); initializedRef.current = false }
  }, [initViewport, setViewport])

  // Render on every state change
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')!
    const dpr = window.devicePixelRatio || 1
    ctx.save()
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    renderTimeline(ctx, viewport, records, hoverRecordId, selectedRecordId, palette, theme)
    ctx.restore()
  }, [viewport, records, hoverRecordId, selectedRecordId, palette, theme])

  const handleHover = useCallback((id: string | null) => {
    setHoverRecordId(id)
  }, [])

  const handleClick = useCallback((record: ApiRecord | null) => {
    onSelectRecord(record?.id ?? null)
  }, [onSelectRecord])

  const { onWheel, onMouseDown, onMouseMove, onMouseUp, onMouseLeave } = useCanvasEvents({
    records, viewport, zoom, pan,
    onHover: handleHover,
    onClick: handleClick,
  })

  // Attach wheel with non-passive listener
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    canvas.addEventListener('wheel', onWheel, { passive: false })
    return () => canvas.removeEventListener('wheel', onWheel)
  }, [onWheel])

  // Touch: 1-finger drag = pan, 2-finger = pinch-zoom, tap = select. zoom/pan
  // are stable (useCallback) and onSelectRecord is memoized, so this attaches
  // once and is not torn down mid-gesture. preventDefault + touch-action:none
  // stop the browser's own scroll/zoom and synthesized mouse events.
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const g = touchRef.current
    const distOf = (a: Touch, b: Touch) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY)

    const onStart = (e: TouchEvent) => {
      if (e.touches.length === 1) {
        const t = e.touches[0]
        g.mode = 'pan'; g.lastX = t.clientX; g.startX = t.clientX; g.startY = t.clientY; g.moved = false
      } else if (e.touches.length >= 2) {
        g.mode = 'pinch'; g.lastDist = distOf(e.touches[0], e.touches[1])
        setHoverRecordId(null)
      }
      e.preventDefault()
    }
    const onMove = (e: TouchEvent) => {
      if (g.mode === 'pan' && e.touches.length === 1) {
        const t = e.touches[0]
        if (Math.abs(t.clientX - g.startX) > 6 || Math.abs(t.clientY - g.startY) > 6) g.moved = true
        pan(t.clientX - g.lastX)
        g.lastX = t.clientX
        e.preventDefault()
      } else if (g.mode === 'pinch' && e.touches.length >= 2) {
        const rect = canvas.getBoundingClientRect()
        const midX = (e.touches[0].clientX + e.touches[1].clientX) / 2 - rect.left
        const newDist = distOf(e.touches[0], e.touches[1])
        if (newDist > 0 && g.lastDist > 0) zoom(g.lastDist / newDist, midX)
        g.lastDist = newDist
        e.preventDefault()
      }
    }
    const onEnd = (e: TouchEvent) => {
      if (g.mode === 'pan' && !g.moved && e.changedTouches.length >= 1) {
        const t = e.changedTouches[0]
        const rect = canvas.getBoundingClientRect()
        const hit = hitTest(t.clientX - rect.left, t.clientY - rect.top, recordsRef.current, viewportRef.current)
        onSelectRecord(hit?.record.id ?? null)
      }
      if (e.touches.length === 0) g.mode = 'none'
      else if (e.touches.length === 1) { g.mode = 'pan'; g.lastX = e.touches[0].clientX; g.moved = true }
    }
    canvas.addEventListener('touchstart', onStart, { passive: false })
    canvas.addEventListener('touchmove', onMove, { passive: false })
    canvas.addEventListener('touchend', onEnd)
    canvas.addEventListener('touchcancel', onEnd)
    return () => {
      canvas.removeEventListener('touchstart', onStart)
      canvas.removeEventListener('touchmove', onMove)
      canvas.removeEventListener('touchend', onEnd)
      canvas.removeEventListener('touchcancel', onEnd)
    }
  }, [zoom, pan, onSelectRecord])

  const hoverRecord = hoverRecordId ? records.find(r => r.id === hoverRecordId) : null

  return (
    <div style={{ position: 'relative', width: '100%' }}>
      <div
        ref={containerRef}
        style={{
          width: '100%',
          cursor: hoverRecordId ? 'pointer' : 'default',
          borderRadius: 'var(--radius-lg)',
          border: '1px solid var(--bg-border)',
          overflow: 'hidden',
          boxShadow: 'var(--shadow-sm)',
        }}
      >
        <canvas
          ref={canvasRef}
          role="img"
          aria-label={`活动时间轴，共 ${records.length} 条活动。键盘用户可用下方列表逐条查看。`}
          style={{ display: 'block', userSelect: 'none', touchAction: 'none' }}
          onMouseDown={e => onMouseDown(e.nativeEvent)}
          onMouseMove={e => {
            onMouseMove(e.nativeEvent)
            if (hoverRecordId) {
              setTooltipPos({ x: e.clientX, y: e.clientY })
            } else {
              setTooltipPos(null)
            }
          }}
          onMouseUp={e => onMouseUp(e.nativeEvent)}
          onMouseLeave={() => {
            onMouseLeave()
            setTooltipPos(null)
          }}
        />
      </div>

      {/* Zoom controls — floating pill group */}
      <div
        style={{
          position: 'absolute',
          bottom: -44,
          right: 0,
          zIndex: 10,
          display: 'inline-flex',
          alignItems: 'center',
          gap: 2,
          padding: 3,
          borderRadius: 'var(--radius-pill)',
          background: 'var(--bg-surface)',
          border: '1px solid var(--bg-border)',
          boxShadow: 'var(--shadow-sm)',
        }}
      >
        <button aria-label="缩小" title="缩小" onClick={() => zoom(1.5, viewport.canvasWidth / 2)} style={iconBtn}>−</button>
        <button onClick={() => goToday(viewport.canvasWidth)} style={textBtn}>整天</button>
        <button aria-label="放大" title="放大" onClick={() => zoom(0.67, viewport.canvasWidth / 2)} style={iconBtn}>+</button>
      </div>

      {/* Keyboard / screen-reader path into the canvas: the <canvas> itself is
          mouse-only, so mirror each activity as a focusable button that drives
          the same selection (→ populates the detail panel). Visually hidden but
          in the tab order (WCAG 2.1.1). */}
      <ul style={srOnly}>
        {records.map((rec) => (
          <li key={rec.id}>
            <button
              type="button"
              onClick={() => onSelectRecord(rec.id)}
            >
              {`${formatTime(rec.ts_start)} ${rec.app_name}${rec.window_title ? ' — ' + rec.window_title : ''}`}
            </button>
          </li>
        ))}
      </ul>

      {hoverRecord && tooltipPos && (
        <TimelineTooltip record={hoverRecord} x={tooltipPos.x} y={tooltipPos.y} />
      )}
    </div>
  )
}

/** Visually hidden but still focusable/announced — standard sr-only pattern. */
const srOnly: React.CSSProperties = {
  position: 'absolute',
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: 'hidden',
  clip: 'rect(0, 0, 0, 0)',
  whiteSpace: 'nowrap',
  border: 0,
}

const iconBtn: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: 26,
  height: 26,
  background: 'transparent',
  border: 'none',
  color: 'var(--text-secondary)',
  borderRadius: 'var(--radius-pill)',
  cursor: 'pointer',
  fontSize: 16,
  lineHeight: 1,
}

const textBtn: React.CSSProperties = {
  height: 26,
  padding: '0 10px',
  background: 'transparent',
  border: 'none',
  color: 'var(--text-secondary)',
  borderRadius: 'var(--radius-pill)',
  cursor: 'pointer',
  fontSize: 12,
  fontWeight: 500,
}
