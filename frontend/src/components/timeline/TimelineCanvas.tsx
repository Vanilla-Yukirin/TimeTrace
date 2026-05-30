import { useRef, useEffect, useState, useCallback } from 'react'
import type { ApiRecord } from '@/types/api'
import { useTimelineState } from '@/hooks/useTimelineState'
import { renderTimeline, CANVAS_H } from './useCanvasRenderer'
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
    renderTimeline(ctx, viewport, records, hoverRecordId, selectedRecordId)
    ctx.restore()
  }, [viewport, records, hoverRecordId, selectedRecordId])

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
      <div ref={containerRef} style={{ width: '100%', cursor: hoverRecordId ? 'pointer' : 'default' }}>
        <canvas
          ref={canvasRef}
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

      {/* Zoom controls */}
      <div
        className="flex items-center gap-1"
        style={{ position: 'absolute', bottom: -28, right: 8, zIndex: 10 }}
      >
        <button onClick={() => zoom(1.5, viewport.canvasWidth / 2)}
          style={btnStyle}>−</button>
        <button onClick={() => goToday(viewport.canvasWidth)}
          style={{ ...btnStyle, fontSize: 10, padding: '2px 6px' }}>整天</button>
        <button onClick={onGoToday}
          style={{ ...btnStyle, fontSize: 10, padding: '2px 6px' }}>今天</button>
        <button onClick={() => zoom(0.67, viewport.canvasWidth / 2)}
          style={btnStyle}>+</button>
      </div>

      {hoverRecord && tooltipPos && (
        <TimelineTooltip record={hoverRecord} x={tooltipPos.x} y={tooltipPos.y} />
      )}
    </div>
  )
}

const btnStyle: React.CSSProperties = {
  background: 'var(--bg-raised)',
  border: '1px solid var(--bg-border)',
  color: 'var(--text-secondary)',
  borderRadius: 4,
  padding: '2px 8px',
  cursor: 'pointer',
  fontSize: 13,
  lineHeight: '18px',
}
