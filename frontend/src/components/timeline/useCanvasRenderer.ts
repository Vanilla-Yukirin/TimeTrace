import type { ApiRecord } from '@/types/api'
import type { TimelineViewport } from '@/hooks/useTimelineState'
import type { Theme } from '@/contexts/theme'
import { getAppColor } from '@/lib/colorMap'
import { formatTime, formatHM } from '@/lib/dateUtils'

const LANE_Y_AXIS = 0       // top of time axis labels
const AXIS_H = 28
const ACTIVITY_Y = AXIS_H + 6
const ACTIVITY_H = 42
const FRAMES_Y = ACTIVITY_Y + ACTIVITY_H + 12
const FRAMES_H = 18
export const CANVAS_H = FRAMES_Y + FRAMES_H + 16

const BLOCK_RADIUS = 7

/** Canvas can't read CSS vars, so the timeline keeps its own palette per theme.
 *  Keep these in sync with the --timeline-* tokens in index.css. Selecting by
 *  the `theme` value (not getComputedStyle) sidesteps effect-ordering races on
 *  theme flips. */
export interface TimelinePalette {
  bg: string
  grid: string
  axis: string
  laneLabel: string
  selBorder: string
  frame: string
  frameSel: string
}

export const TIMELINE_PALETTES: Record<Theme, TimelinePalette> = {
  dark: {
    bg: '#10131d',
    grid: 'rgba(255, 255, 255, 0.055)',
    axis: '#5d6781',
    laneLabel: '#46506a',
    selBorder: '#e7ebf4',
    frame: '#38bdf8',
    frameSel: '#7c8cff',
  },
  light: {
    bg: '#f1f4fc',
    grid: 'rgba(30, 40, 80, 0.07)',
    axis: '#94a3b8',
    laneLabel: '#aab2c6',
    selBorder: '#1d2235',
    frame: '#0ea5e9',
    frameSel: '#5b6ef5',
  },
}

interface HitResult {
  record: ApiRecord
  lane: 'activity' | 'frames'
}

// "Nice" tick intervals, ascending. The renderer picks the smallest one whose
// on-screen width clears MIN_TICK_PX, so labels never crowd — when zoomed out
// (or on a narrow phone) it naturally steps up to coarser intervals (…1h→2h→
// 3h→6h→12h) instead of cramming every hour together.
const TICK_INTERVALS_MS = [
  60_000,            // 1 min
  5 * 60_000,        // 5 min
  10 * 60_000,       // 10 min
  15 * 60_000,       // 15 min
  30 * 60_000,       // 30 min
  60 * 60_000,       // 1 h
  2 * 60 * 60_000,   // 2 h
  3 * 60 * 60_000,   // 3 h
  6 * 60 * 60_000,   // 6 h
  12 * 60 * 60_000,  // 12 h
]
const MIN_TICK_PX = 58 // minimum horizontal gap between labeled ticks

/** Smallest "nice" interval whose width ≥ MIN_TICK_PX at the current scale. */
function tickInterval(scaleMs: number): number {
  for (const iv of TICK_INTERVALS_MS) {
    if (iv / scaleMs >= MIN_TICK_PX) return iv
  }
  return TICK_INTERVALS_MS[TICK_INTERVALS_MS.length - 1]
}

/** roundRect with a graceful fallback for older canvas impls. */
function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number, r: number,
): void {
  const rad = Math.min(r, w / 2, h / 2)
  if (typeof ctx.roundRect === 'function') {
    ctx.beginPath()
    ctx.roundRect(x, y, w, h, rad)
    return
  }
  ctx.beginPath()
  ctx.moveTo(x + rad, y)
  ctx.arcTo(x + w, y, x + w, y + h, rad)
  ctx.arcTo(x + w, y + h, x, y + h, rad)
  ctx.arcTo(x, y + h, x, y, rad)
  ctx.arcTo(x, y, x + w, y, rad)
  ctx.closePath()
}

export function renderTimeline(
  ctx: CanvasRenderingContext2D,
  vp: TimelineViewport,
  records: ApiRecord[],
  hoverRecordId: string | null,
  selectedRecordId: string | null,
  palette: TimelinePalette,
  theme: Theme,
): void {
  const { canvasWidth: w, scaleMs, offsetMs } = vp
  const h = CANVAS_H
  const isLight = theme === 'light'

  // Background
  ctx.fillStyle = palette.bg
  ctx.fillRect(0, 0, w, h)

  // --- Time axis ---
  const interval = tickInterval(scaleMs)
  const firstTick = Math.ceil(offsetMs / interval) * interval
  ctx.font = '10px JetBrains Mono, Consolas, monospace'
  ctx.textAlign = 'center'

  for (let ts = firstTick; ts < offsetMs + w * scaleMs; ts += interval) {
    const x = (ts - offsetMs) / scaleMs
    // Grid line
    ctx.strokeStyle = palette.grid
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(x, AXIS_H)
    ctx.lineTo(x, h)
    ctx.stroke()
    // Label — drop seconds at minute+ intervals so it stays narrow.
    ctx.fillStyle = palette.axis
    ctx.fillText(interval < 60_000 ? formatTime(ts) : formatHM(ts), x, LANE_Y_AXIS + 14)
  }

  // Lane labels
  ctx.textAlign = 'left'
  ctx.font = '10px Inter, system-ui, sans-serif'
  ctx.fillStyle = palette.laneLabel
  ctx.fillText('活动', 4, ACTIVITY_Y - 4)
  ctx.fillText('截图', 4, FRAMES_Y - 4)

  // --- Activity lane ---
  for (const rec of records) {
    const x1 = (rec.ts_start - offsetMs) / scaleMs
    const x2 = rec.ts_end ? (rec.ts_end - offsetMs) / scaleMs : (Date.now() - offsetMs) / scaleMs
    const w2 = Math.max(x2 - x1, 2)

    if (x1 > w || x2 < 0) continue

    const baseColor = getAppColor(rec.app_name)
    const selected = rec.id === selectedRecordId
    const hovered = rec.id === hoverRecordId

    // App colors are mid-saturation; on the light canvas, drawing them faint
    // (the dark-theme alpha) made them wash out, so bump alpha in light mode.
    roundRect(ctx, x1, ACTIVITY_Y, w2, ACTIVITY_H, BLOCK_RADIUS)
    ctx.fillStyle = baseColor
    ctx.globalAlpha = selected ? 1.0 : hovered ? (isLight ? 1.0 : 0.92) : isLight ? 0.9 : 0.78
    ctx.fill()
    ctx.globalAlpha = 1.0

    // subtle top sheen for depth (only on blocks wide enough to notice)
    if (w2 > 10) {
      roundRect(ctx, x1, ACTIVITY_Y, w2, ACTIVITY_H * 0.5, BLOCK_RADIUS)
      ctx.fillStyle = isLight ? 'rgba(255,255,255,0.30)' : 'rgba(255,255,255,0.10)'
      ctx.globalAlpha = selected ? 0.9 : hovered ? 0.7 : 0.45
      ctx.fill()
      ctx.globalAlpha = 1.0
    }

    // Hover overlay — lighten on dark, darken on light so it reads either way.
    if (hovered && !selected) {
      roundRect(ctx, x1, ACTIVITY_Y, w2, ACTIVITY_H, BLOCK_RADIUS)
      ctx.fillStyle = isLight ? 'rgba(0,0,0,0.07)' : 'rgba(255,255,255,0.14)'
      ctx.fill()
    }

    // Selected: bright border + glow
    if (selected) {
      ctx.save()
      roundRect(ctx, x1 + 1, ACTIVITY_Y + 1, w2 - 2, ACTIVITY_H - 2, BLOCK_RADIUS - 1)
      ctx.strokeStyle = palette.selBorder
      ctx.lineWidth = 2
      ctx.shadowColor = baseColor
      ctx.shadowBlur = 10
      ctx.stroke()
      ctx.restore()
    }
  }

  // --- Frames lane (screenshot markers) ---
  for (const rec of records) {
    if (!rec.screenshot_count) continue
    const cx = (rec.ts_start - offsetMs) / scaleMs
    if (cx < -4 || cx > w + 4) continue

    const cy = FRAMES_Y + FRAMES_H / 2
    const size = 5
    ctx.fillStyle = rec.id === selectedRecordId ? palette.frameSel : palette.frame
    ctx.globalAlpha = 0.92
    ctx.beginPath()
    ctx.moveTo(cx, cy - size)
    ctx.lineTo(cx + size, cy)
    ctx.lineTo(cx, cy + size)
    ctx.lineTo(cx - size, cy)
    ctx.closePath()
    ctx.fill()
    ctx.globalAlpha = 1.0
  }
}

export function hitTest(
  mouseX: number,
  mouseY: number,
  records: ApiRecord[],
  vp: TimelineViewport,
): HitResult | null {
  const { scaleMs, offsetMs } = vp

  // Check activity lane first
  if (mouseY >= ACTIVITY_Y && mouseY <= ACTIVITY_Y + ACTIVITY_H) {
    for (const rec of records) {
      const x1 = (rec.ts_start - offsetMs) / scaleMs
      const x2 = rec.ts_end ? (rec.ts_end - offsetMs) / scaleMs : (Date.now() - offsetMs) / scaleMs
      if (mouseX >= x1 && mouseX <= Math.max(x2, x1 + 2)) {
        return { record: rec, lane: 'activity' }
      }
    }
  }

  // Check frames lane
  if (mouseY >= FRAMES_Y - 8 && mouseY <= FRAMES_Y + FRAMES_H + 8) {
    let best: ApiRecord | null = null
    let bestDist = 10
    for (const rec of records) {
      if (!rec.screenshot_count) continue
      const cx = (rec.ts_start - offsetMs) / scaleMs
      const dist = Math.abs(mouseX - cx)
      if (dist < bestDist) { bestDist = dist; best = rec }
    }
    if (best) return { record: best, lane: 'frames' }
  }

  return null
}
