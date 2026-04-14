import type { ApiRecord } from '@/types/api'
import type { TimelineViewport } from '@/hooks/useTimelineState'
import { getAppColor } from '@/lib/colorMap'
import { formatTime } from '@/lib/dateUtils'

const LANE_Y_AXIS = 0       // top of time axis labels
const AXIS_H = 28
const ACTIVITY_Y = AXIS_H + 4
const ACTIVITY_H = 40
const FRAMES_Y = ACTIVITY_Y + ACTIVITY_H + 10
const FRAMES_H = 18
export const CANVAS_H = FRAMES_Y + FRAMES_H + 16

interface HitResult {
  record: ApiRecord
  lane: 'activity' | 'frames'
}

/** Determine the tick interval (in ms) given current scale */
function tickInterval(scaleMs: number): number {
  const minsPerPx = scaleMs / 60_000
  if (minsPerPx <= 0.5)  return 5 * 60_000    // 5 min
  if (minsPerPx <= 2)    return 15 * 60_000   // 15 min
  if (minsPerPx <= 5)    return 30 * 60_000   // 30 min
  return 60 * 60_000                           // 1 hour
}

export function renderTimeline(
  ctx: CanvasRenderingContext2D,
  vp: TimelineViewport,
  records: ApiRecord[],
  hoverRecordId: string | null,
  selectedRecordId: string | null,
): void {
  const { canvasWidth: w, scaleMs, offsetMs } = vp
  const h = CANVAS_H

  // Background
  ctx.fillStyle = '#141720'
  ctx.fillRect(0, 0, w, h)

  // --- Time axis ---
  const interval = tickInterval(scaleMs)
  const firstTick = Math.ceil(offsetMs / interval) * interval
  ctx.font = '10px JetBrains Mono, Consolas, monospace'
  ctx.textAlign = 'center'

  for (let ts = firstTick; ts < offsetMs + w * scaleMs; ts += interval) {
    const x = (ts - offsetMs) / scaleMs
    // Grid line
    ctx.strokeStyle = '#1e2235'
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(x, AXIS_H)
    ctx.lineTo(x, h)
    ctx.stroke()
    // Label
    ctx.fillStyle = '#64748b'
    ctx.fillText(formatTime(ts), x, LANE_Y_AXIS + 14)
  }

  // Lane labels
  ctx.textAlign = 'left'
  ctx.font = '10px Inter, system-ui, sans-serif'
  ctx.fillStyle = '#475569'
  ctx.fillText('活动', 4, ACTIVITY_Y - 3)
  ctx.fillText('截图', 4, FRAMES_Y - 3)

  // --- Activity lane ---
  for (const rec of records) {
    const x1 = (rec.ts_start - offsetMs) / scaleMs
    const x2 = rec.ts_end ? (rec.ts_end - offsetMs) / scaleMs : (Date.now() - offsetMs) / scaleMs
    const w2 = Math.max(x2 - x1, 2)

    if (x1 > w || x2 < 0) continue

    const baseColor = getAppColor(rec.app_name)
    ctx.fillStyle = baseColor
    ctx.globalAlpha = rec.id === selectedRecordId ? 1.0 : rec.id === hoverRecordId ? 0.9 : 0.75
    ctx.fillRect(x1, ACTIVITY_Y, w2, ACTIVITY_H)
    ctx.globalAlpha = 1.0

    // Selected: white border
    if (rec.id === selectedRecordId) {
      ctx.strokeStyle = '#ffffff'
      ctx.lineWidth = 2
      ctx.strokeRect(x1 + 1, ACTIVITY_Y + 1, w2 - 2, ACTIVITY_H - 2)
    }

    // Hover: lighter overlay
    if (rec.id === hoverRecordId && rec.id !== selectedRecordId) {
      ctx.fillStyle = 'rgba(255,255,255,0.15)'
      ctx.fillRect(x1, ACTIVITY_Y, w2, ACTIVITY_H)
    }
  }

  // --- Frames lane (screenshot markers) ---
  for (const rec of records) {
    if (!rec.screenshot_count) continue
    const cx = (rec.ts_start - offsetMs) / scaleMs
    if (cx < -4 || cx > w + 4) continue

    const cy = FRAMES_Y + FRAMES_H / 2
    const size = 5
    ctx.fillStyle = rec.id === selectedRecordId ? '#60a5fa' : '#38bdf8'
    ctx.globalAlpha = 0.9
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
