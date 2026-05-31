import { useRef, useEffect, useState, useCallback } from 'react'
import type { ApiRecord } from '@/types/api'
import { useTimelineState } from '@/hooks/useTimelineState'
import { useTheme } from '@/contexts/ThemeContext'
import { formatTime } from '@/lib/dateUtils'
import { renderTimeline, CANVAS_H, TIMELINE_PALETTES } from './useCanvasRenderer'
import { useCanvasEvents } from './useCanvasEvents'
import { TimelineTooltip } from './TimelineTooltip'

interface TimelineCanvasProps {
  records: ApiRecord[]
  date: Date
  selectedRecordId: string | null
  onSelectRecord: (id: string | null) => void
  onGoToday: () => void
}

export function TimelineCanvas({
  records, date, selectedRecordId, onSelectRecord, onGoToday,
}: TimelineCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [hoverRecordId, setHoverRecordId] = useState<string | null>(null)
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number } | null>(null)

  const { viewport, setViewport, initViewport, zoom, pan, goToday } = useTimelineState(date)
  const { theme } = useTheme()
  const palette = TIMELINE_PALETTES[theme]

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
          style={{ display: 'block', userSelect: 'none' }}
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
        <button onClick={onGoToday} style={textBtn}>今天</button>
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
