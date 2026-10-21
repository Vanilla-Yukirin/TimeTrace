import { memo, useRef, useEffect, useLayoutEffect, useState, useCallback, useMemo } from 'react'
import type { ApiRecord } from '@/types/api'
import { useTimelineState } from '@/hooks/useTimelineState'
import { useTheme } from '@/contexts/theme'
import { formatTime } from '@/lib/dateUtils'
import { UNKNOWN_DEVICE } from '@/hooks/useDevices'
import {
  renderTimeline, hitTest, buildTimelineModel, visibleRange, canvasHeight, recordEnd,
  TIMELINE_PALETTES,
} from './useCanvasRenderer'
import { useCanvasEvents } from './useCanvasEvents'
import { TimelineTooltip } from './TimelineTooltip'

interface TimelineCanvasProps {
  records: ApiRecord[]
  date: Date
  selectedRecordId: string | null
  onSelectRecord: (id: string | null) => void
  /** Device keys in lane order (uuid or UNKNOWN_DEVICE); stable reference. */
  deviceOrder?: string[]
  /** Device key → display label; stable reference. */
  deviceLabels?: Record<string, string>
}

const EMPTY_ORDER: string[] = []
const EMPTY_LABELS: Record<string, string> = {}
// Taller than this (many device lanes) → the canvas box scrolls vertically.
const MAX_VISIBLE_H = 420

function deviceKey(rec: ApiRecord): string {
  return rec.device_id ?? UNKNOWN_DEVICE
}

// Cap for the screen-reader mirror list: only the records in view are mirrored
// (a full day is thousands of buttons, re-rendered on every viewport change).
const SR_LIST_MAX = 200
// How often an open (still-running) record's bar is re-extended to "now".
const OPEN_RECORD_REDRAW_MS = 30_000

export const TimelineCanvas = memo(function TimelineCanvas({
  records, date, selectedRecordId, onSelectRecord,
  deviceOrder = EMPTY_ORDER, deviceLabels = EMPTY_LABELS,
}: TimelineCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  // Hovered record + the pointer position when it became hovered (the tooltip's
  // initial placement). Only changes when the hovered record changes.
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null)
  const hoverRecordId = hover?.id ?? null
  // Tooltip follows the pointer by direct DOM writes (no React state per move).
  const tooltipElRef = useRef<HTMLDivElement>(null)
  const pointerRef = useRef({ x: 0, y: 0 })

  const model = useMemo(
    () => buildTimelineModel(records, deviceKey, deviceOrder, (k) => deviceLabels[k] ?? (k === UNKNOWN_DEVICE ? '未知设备' : k.slice(0, 8))),
    [records, deviceOrder, deviceLabels],
  )
  const height = canvasHeight(model.lanes.length)

  const { viewport, setViewport, initViewport, zoom, pan, goToday } = useTimelineState(date)
  const { theme } = useTheme()
  const palette = TIMELINE_PALETTES[theme]

  // Touch gesture state persists across re-renders (the render effect re-runs on
  // every pan/zoom, so this can't live in the touch effect's closure). records /
  // viewport are read via refs so tap-hit-testing always sees current values.
  const touchRef = useRef({ mode: 'none' as 'none' | 'pan' | 'pinch', lastX: 0, lastDist: 0, startX: 0, startY: 0, moved: false })
  const modelRef = useRef(model)
  const viewportRef = useRef(viewport)

  useLayoutEffect(() => {
    modelRef.current = model
    viewportRef.current = viewport
  }, [model, viewport])

  // Track whether the canvas has been initialized at least once
  const initializedRef = useRef(false)

  // Backing-store size follows container width (ResizeObserver) and the lane
  // count (height). Do NOT call ctx.scale here — draw() applies setTransform.
  const widthRef = useRef(0)
  const heightRef = useRef(height)
  const applySize = useCallback((width: number, h: number) => {
    const canvas = canvasRef.current
    if (!canvas || width <= 0) return
    const dpr = window.devicePixelRatio || 1
    canvas.width = width * dpr
    canvas.height = h * dpr
    canvas.style.width = `${width}px`
    canvas.style.height = `${h}px`
  }, [])
  useLayoutEffect(() => {
    heightRef.current = height
    applySize(widthRef.current, height)
  }, [height, applySize])

  // Init on mount and resize
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const obs = new ResizeObserver(entries => {
      const { width } = entries[0].contentRect
      widthRef.current = width
      applySize(width, heightRef.current)
      if (!initializedRef.current) {
        initializedRef.current = true
        initViewport(width)
      } else {
        setViewport(v => ({ ...v, canvasWidth: width }))
      }
    })
    obs.observe(el)
    return () => { obs.disconnect(); initializedRef.current = false }
  }, [initViewport, setViewport, applySize])

  const draw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')!
    const dpr = window.devicePixelRatio || 1
    ctx.save()
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    renderTimeline(ctx, viewport, model, hoverRecordId, selectedRecordId, palette, theme)
    ctx.restore()
  }, [viewport, model, hoverRecordId, selectedRecordId, palette, theme])

  // Keep the selected record in view: when the selection changes (click,
  // keyboard ←/→, ?highlight= on load) and the record is off-screen, pan so it
  // sits a third of the way in. Zoom is left alone.
  useEffect(() => {
    if (!selectedRecordId) return
    const rec = modelRef.current.all.items.find((r) => r.id === selectedRecordId)
    const vp = viewportRef.current
    if (!rec || vp.canvasWidth <= 0) return
    const viewEnd = vp.offsetMs + vp.canvasWidth * vp.scaleMs
    const end = recordEnd(rec, Date.now())
    if (rec.ts_start >= vp.offsetMs && end <= viewEnd) return
    if (end > vp.offsetMs && rec.ts_start < viewEnd) return // partly visible is enough
    setViewport((v) => ({ ...v, offsetMs: rec.ts_start - (v.canvasWidth / 3) * v.scaleMs }))
  }, [selectedRecordId, model, setViewport])

  // Render on every state change
  useEffect(() => {
    draw()
  }, [draw])

  // An open record's bar ends at "now": re-extend it on a slow canvas-only
  // timer instead of a page-level clock that re-renders React every second.
  const hasOpenRecord = useMemo(() => records.some((r) => r.ts_end == null), [records])
  useEffect(() => {
    if (!hasOpenRecord) return
    const id = window.setInterval(draw, OPEN_RECORD_REDRAW_MS)
    return () => window.clearInterval(id)
  }, [hasOpenRecord, draw])

  const handleHover = useCallback((id: string | null) => {
    const { x, y } = pointerRef.current
    setHover((prev) => (id == null ? null : prev?.id === id ? prev : { id, x, y }))
  }, [])

  const handlePointer = useCallback((x: number, y: number) => {
    pointerRef.current = { x, y }
    const el = tooltipElRef.current
    if (el) {
      el.style.left = `${x + 14}px`
      el.style.top = `${y - 10}px`
    }
  }, [])

  const handleClick = useCallback((record: ApiRecord | null) => {
    onSelectRecord(record?.id ?? null)
  }, [onSelectRecord])

  const { onWheel, onMouseDown, onMouseMove, onMouseUp, onMouseLeave } = useCanvasEvents({
    model, viewport, zoom, pan,
    onHover: handleHover,
    onClick: handleClick,
    onPointer: handlePointer,
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
        setHover(null)
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
        const hit = hitTest(t.clientX - rect.left, t.clientY - rect.top, modelRef.current, viewportRef.current)
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

  const hoverRecord = useMemo(
    () => (hoverRecordId ? records.find(r => r.id === hoverRecordId) ?? null : null),
    [hoverRecordId, records],
  )

  const srRecords = useMemo(() => {
    const [lo, hi] = visibleRange(model.all, viewport, 0)
    return model.all.items.slice(lo, Math.min(hi, lo + SR_LIST_MAX))
  }, [model, viewport])

  return (
    <div style={{ position: 'relative', width: '100%' }}>
      <div
        ref={containerRef}
        style={{
          width: '100%',
          cursor: hoverRecordId ? 'pointer' : 'default',
          borderRadius: 'var(--radius-lg)',
          border: '1px solid var(--bg-border)',
          // Many device lanes: scroll vertically (wheel over the left label
          // gutter, or the scrollbar); the time axis zoom/pan stays on the canvas.
          maxHeight: MAX_VISIBLE_H,
          overflowX: 'hidden',
          overflowY: height > MAX_VISIBLE_H ? 'auto' : 'hidden',
          boxShadow: 'var(--shadow-sm)',
        }}
      >
        <canvas
          ref={canvasRef}
          role="img"
          aria-label={`活动时间轴，共 ${records.length} 条活动。键盘用户可用下方列表逐条查看当前可见范围内的活动。`}
          style={{ display: 'block', userSelect: 'none', touchAction: 'none' }}
          onMouseDown={e => onMouseDown(e.nativeEvent)}
          onMouseMove={e => onMouseMove(e.nativeEvent)}
          onMouseUp={e => onMouseUp(e.nativeEvent)}
          onMouseLeave={() => onMouseLeave()}
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
        <button aria-label="缩小" title="缩小" onClick={() => zoom(1.5, viewport.canvasWidth / 2)} className="tt-nav-row" style={iconBtn}>−</button>
        <button onClick={() => goToday(viewport.canvasWidth)} className="tt-nav-row" style={textBtn}>整天</button>
        <button aria-label="放大" title="放大" onClick={() => zoom(0.67, viewport.canvasWidth / 2)} className="tt-nav-row" style={iconBtn}>+</button>
      </div>

      {/* Keyboard / screen-reader path into the canvas: the <canvas> itself is
          mouse-only, so mirror each activity as a focusable button that drives
          the same selection (→ populates the detail panel). Visually hidden but
          in the tab order (WCAG 2.1.1). */}
      <ul className="sr-only">
        {srRecords.map((rec) => (
          <li key={rec.id}>
            <button
              type="button"
              onClick={() => onSelectRecord(rec.id)}
            >
              {`${formatTime(rec.ts_start)} ${model.lanes.length > 1 ? `[${deviceLabels[deviceKey(rec)] ?? '未知设备'}] ` : ''}${rec.app_name}${rec.window_title ? ' — ' + rec.window_title : ''}`}
            </button>
          </li>
        ))}
      </ul>

      {hoverRecord && hover && (
        <TimelineTooltip
          record={hoverRecord}
          x={hover.x}
          y={hover.y}
          elRef={tooltipElRef}
        />
      )}
    </div>
  )
})

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
