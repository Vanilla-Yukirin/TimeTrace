import type { ApiRecord } from '@/types/api'
import type { TimelineViewport } from '@/hooks/useTimelineState'
import type { Theme } from '@/contexts/theme'
import { getAppColor } from '@/lib/colorMap'
import { formatTime, formatHM } from '@/lib/dateUtils'

const LANE_Y_AXIS = 0       // top of time axis labels
const AXIS_H = 28
const ACTIVITY_Y = AXIS_H + 6
const ACTIVITY_H = 42
// Vertical distance between two device lanes: block height + room for the
// next lane's label (drawn 4px above its blocks).
const LANE_STRIDE = ACTIVITY_H + 18
const FRAMES_H = 18

/** y of lane ``i``'s activity blocks. */
function laneY(i: number): number {
  return ACTIVITY_Y + i * LANE_STRIDE
}

/** y of the shared screenshot-marker row below ``laneCount`` lanes. */
function framesY(laneCount: number): number {
  return laneY(Math.max(1, laneCount) - 1) + ACTIVITY_H + 12
}

/** Canvas height for ``laneCount`` device lanes (1 lane = the classic layout). */
export function canvasHeight(laneCount: number): number {
  return framesY(laneCount) + FRAMES_H + 16
}
export const CANVAS_H = canvasHeight(1)

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

/** Records sorted by ``ts_start``. ``maxClosedSpanMs`` bounds how far left of
 *  the viewport a closed record can start and still be visible; open records
 *  (still running, extend to now) are tracked by the earliest one's index. */
export interface SortedRecords {
  items: ApiRecord[]
  maxClosedSpanMs: number
  firstOpenIdx: number // -1 when every record is closed
}

/** One activity lane per device. ``label`` null = single-device layout (the
 *  lane is just "活动", no device name). */
export interface TimelineLane {
  key: string
  label: string | null
  sorted: SortedRecords
}

/** Everything the renderer / hit-test needs: per-device lanes plus all records
 *  merged (for the shared screenshot-marker row). */
export interface TimelineModel {
  lanes: TimelineLane[]
  all: SortedRecords
}

export function recordEnd(rec: ApiRecord, now: number): number {
  return rec.ts_end ?? now
}

export function sortRecords(records: ApiRecord[]): SortedRecords {
  const items = [...records].sort((a, b) => a.ts_start - b.ts_start)
  let maxClosedSpanMs = 0
  let firstOpenIdx = -1
  items.forEach((r, i) => {
    if (r.ts_end == null) {
      if (firstOpenIdx < 0) firstOpenIdx = i
    } else {
      maxClosedSpanMs = Math.max(maxClosedSpanMs, r.ts_end - r.ts_start)
    }
  })
  return { items, maxClosedSpanMs, firstOpenIdx }
}

/** Build lanes from ``records``. ``laneOrder`` lists device keys in display
 *  order (unknown keys are appended); ``labelOf`` maps a key to its label.
 *  Lanes without records are omitted; a single lane gets no device label. */
export function buildTimelineModel(
  records: ApiRecord[],
  keyOf: (rec: ApiRecord) => string,
  laneOrder: string[],
  labelOf: (key: string) => string,
): TimelineModel {
  const groups = new Map<string, ApiRecord[]>()
  for (const r of records) {
    const k = keyOf(r)
    const g = groups.get(k)
    if (g) g.push(r)
    else groups.set(k, [r])
  }
  const order = [...laneOrder.filter((k) => groups.has(k)), ...[...groups.keys()].filter((k) => !laneOrder.includes(k))]
  const multi = order.length > 1
  return {
    lanes: order.map((k) => ({ key: k, label: multi ? labelOf(k) : null, sorted: sortRecords(groups.get(k)!) })),
    all: sortRecords(records),
  }
}

/** First index whose ts_start >= ts (binary search over sorted items). */
function lowerBound(items: ApiRecord[], ts: number): number {
  let lo = 0
  let hi = items.length
  while (lo < hi) {
    const mid = (lo + hi) >>> 1
    if (items[mid].ts_start < ts) lo = mid + 1
    else hi = mid
  }
  return lo
}

/** Half-open index range [lo, hi) of records that may intersect [fromMs, toMs].
 *  Conservative: an open record extends to "now", so the range reaches back to
 *  the earliest open record that starts before ``toMs``. */
export function rangeIn(sorted: SortedRecords, fromMs: number, toMs: number): [number, number] {
  let lo = lowerBound(sorted.items, fromMs - sorted.maxClosedSpanMs)
  const hi = lowerBound(sorted.items, toMs + 1)
  if (sorted.firstOpenIdx >= 0 && sorted.firstOpenIdx < Math.min(lo, hi)) lo = sorted.firstOpenIdx
  return [lo, hi]
}

/** Records intersecting the viewport (plus a small pixel margin). */
export function visibleRange(sorted: SortedRecords, vp: TimelineViewport, marginPx = 8): [number, number] {
  const from = vp.offsetMs - marginPx * vp.scaleMs
  const to = vp.offsetMs + (vp.canvasWidth + marginPx) * vp.scaleMs
  return rangeIn(sorted, from, to)
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
  model: TimelineModel,
  hoverRecordId: string | null,
  selectedRecordId: string | null,
  palette: TimelinePalette,
  theme: Theme,
): void {
  const { canvasWidth: w, scaleMs, offsetMs } = vp
  const laneCount = Math.max(1, model.lanes.length)
  const h = canvasHeight(laneCount)
  const fy = framesY(laneCount)
  const isLight = theme === 'light'
  const now = Date.now()

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

  // Lane labels (device name per lane when several devices are shown)
  ctx.textAlign = 'left'
  ctx.font = '10px Inter, system-ui, sans-serif'
  ctx.fillStyle = palette.laneLabel
  if (model.lanes.length === 0) ctx.fillText('活动', 4, ACTIVITY_Y - 4)
  model.lanes.forEach((lane, i) => ctx.fillText(lane.label ?? '活动', 4, laneY(i) - 4))
  ctx.fillText('截图', 4, fy - 4)

  // --- Activity lanes ---
  model.lanes.forEach((lane, i) => {
    const [lo, hi] = visibleRange(lane.sorted, vp)
    drawActivityLane(ctx, lane.sorted.items.slice(lo, hi), laneY(i), vp, now, hoverRecordId, selectedRecordId, palette, isLight)
  })

  // --- Frames lane (screenshot markers, shared by all devices) ---
  const [flo, fhi] = visibleRange(model.all, vp)
  for (const rec of model.all.items.slice(flo, fhi)) {
    if (!rec.screenshot_count) continue
    const cx = (rec.ts_start - offsetMs) / scaleMs
    if (cx < -4 || cx > w + 4) continue

    const cy = fy + FRAMES_H / 2
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

function drawActivityLane(
  ctx: CanvasRenderingContext2D,
  records: ApiRecord[],
  laneTop: number,
  vp: TimelineViewport,
  now: number,
  hoverRecordId: string | null,
  selectedRecordId: string | null,
  palette: TimelinePalette,
  isLight: boolean,
): void {
  const { canvasWidth: w, scaleMs, offsetMs } = vp
  for (const rec of records) {
    const x1 = (rec.ts_start - offsetMs) / scaleMs
    const x2 = (recordEnd(rec, now) - offsetMs) / scaleMs
    const w2 = Math.max(x2 - x1, 2)

    if (x1 > w || x2 < 0) continue

    const baseColor = getAppColor(rec.app_name)
    const selected = rec.id === selectedRecordId
    const hovered = rec.id === hoverRecordId

    // App colors are mid-saturation; on the light canvas, drawing them faint
    // (the dark-theme alpha) made them wash out, so bump alpha in light mode.
    roundRect(ctx, x1, laneTop, w2, ACTIVITY_H, BLOCK_RADIUS)
    ctx.fillStyle = baseColor
    ctx.globalAlpha = selected ? 1.0 : hovered ? (isLight ? 1.0 : 0.92) : isLight ? 0.9 : 0.78
    ctx.fill()
    ctx.globalAlpha = 1.0

    // subtle top sheen for depth (only on blocks wide enough to notice)
    if (w2 > 10) {
      roundRect(ctx, x1, laneTop, w2, ACTIVITY_H * 0.5, BLOCK_RADIUS)
      ctx.fillStyle = isLight ? 'rgba(255,255,255,0.30)' : 'rgba(255,255,255,0.10)'
      ctx.globalAlpha = selected ? 0.9 : hovered ? 0.7 : 0.45
      ctx.fill()
      ctx.globalAlpha = 1.0
    }

    // Hover overlay — lighten on dark, darken on light so it reads either way.
    if (hovered && !selected) {
      roundRect(ctx, x1, laneTop, w2, ACTIVITY_H, BLOCK_RADIUS)
      ctx.fillStyle = isLight ? 'rgba(0,0,0,0.07)' : 'rgba(255,255,255,0.14)'
      ctx.fill()
    }

    // Selected: bright border + glow
    if (selected) {
      ctx.save()
      roundRect(ctx, x1 + 1, laneTop + 1, w2 - 2, ACTIVITY_H - 2, BLOCK_RADIUS - 1)
      ctx.strokeStyle = palette.selBorder
      ctx.lineWidth = 2
      ctx.shadowColor = baseColor
      ctx.shadowBlur = 10
      ctx.stroke()
      ctx.restore()
    }
  }
}

export function hitTest(
  mouseX: number,
  mouseY: number,
  model: TimelineModel,
  vp: TimelineViewport,
): HitResult | null {
  const { scaleMs, offsetMs } = vp
  const now = Date.now()
  const laneCount = Math.max(1, model.lanes.length)

  // Which activity lane is under the pointer? Walk newest-start → oldest so the
  // record drawn on top (the later one) wins when blocks overlap.
  const laneIdx = Math.floor((mouseY - ACTIVITY_Y) / LANE_STRIDE)
  const lane = model.lanes[laneIdx]
  if (lane && mouseY >= laneY(laneIdx) && mouseY <= laneY(laneIdx) + ACTIVITY_H) {
    const sorted = lane.sorted
    const ts = offsetMs + mouseX * scaleMs
    const slack = 2 * scaleMs // blocks are drawn at least 2px wide
    const [lo, hi] = rangeIn(sorted, ts - slack, ts)
    for (let i = hi - 1; i >= lo; i--) {
      const rec = sorted.items[i]
      const x1 = (rec.ts_start - offsetMs) / scaleMs
      const x2 = (recordEnd(rec, now) - offsetMs) / scaleMs
      if (mouseX >= x1 && mouseX <= Math.max(x2, x1 + 2)) {
        return { record: rec, lane: 'activity' }
      }
    }
  }

  // Check frames lane (nearest marker within 10px)
  const fy = framesY(laneCount)
  if (mouseY >= fy - 8 && mouseY <= fy + FRAMES_H + 8) {
    const items = model.all.items
    const lo = lowerBound(items, offsetMs + (mouseX - 10) * scaleMs)
    const hi = lowerBound(items, offsetMs + (mouseX + 10) * scaleMs + 1)
    let best: ApiRecord | null = null
    let bestDist = 10
    for (let i = lo; i < hi; i++) {
      const rec = items[i]
      if (!rec.screenshot_count) continue
      const cx = (rec.ts_start - offsetMs) / scaleMs
      const dist = Math.abs(mouseX - cx)
      if (dist < bestDist) { bestDist = dist; best = rec }
    }
    if (best) return { record: best, lane: 'frames' }
  }

  return null
}
