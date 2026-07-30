import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, X } from 'lucide-react'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { categoryColor } from '@/lib/categories'
import { useIsMobile } from '@/hooks/useIsMobile'
import { IconButton } from '@/components/ui/IconButton'
import { Button } from '@/components/ui/Button'
import { ErrorBanner } from '@/components/ui/Feedback'
import { SkeletonRows } from '@/components/ui/Skeleton'
import type { SummaryWindow } from '@/types/api'

// Columns coarse→fine; each is a vertical lane on a SHARED time axis, so a
// child block sits horizontally next to the parent window that contains it.
const COLUMNS: { grain: string; label: string; width: number }[] = [
  { grain: 'week', label: '周', width: 52 },
  { grain: 'day', label: '天', width: 64 },
  { grain: '6h', label: '6时', width: 78 },
  { grain: '1h', label: '时', width: 108 },
  { grain: '5min', label: '5分', width: 168 },
]
const PX_PER_HOUR = 92
const DAY_MS = 24 * 3600 * 1000
const TOTAL_H = (DAY_MS / 3600_000) * PX_PER_HOUR // 24h tall

const CAT_LABELS: Record<string, string> = {
  work: '工作', study: '学习', social: '沟通', entertainment: '娱乐',
  system: '系统', uncategorized: '未分类', _unclassified: '待分类',
}

function fmtHM(ms: number): string {
  const d = new Date(ms)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
function todayLocalLogicalDay(): string {
  const d = new Date(Date.now() - 4 * 3600_000) // before 4AM cut → previous day
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
function shiftDay(day: string, delta: number): string {
  const [y, m, d] = day.split('-').map(Number)
  const dt = new Date(y, m - 1, d + delta)
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`
}

export function PyramidPage() {
  const [day, setDay] = useState(todayLocalLogicalDay())
  const [sel, setSel] = useState<SummaryWindow | null>(null)
  const isMobile = useIsMobile()
  const q = useQuery({
    queryKey: queryKeys.summariesDay(day),
    queryFn: () => api.getSummariesForDay(day),
    refetchInterval: 15000,
    refetchIntervalInBackground: false,
  })
  const dayStart = q.data?.day_start ?? 0
  const total = useMemo(() => {
    const g = q.data?.grains
    if (!g) return 0
    return Object.values(g).reduce((acc, arr) => acc + arr.length, 0)
  }, [q.data])

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', width: '100%' }}>
      {/* Header / day nav */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, padding: isMobile ? '14px 14px 8px' : '16px 22px 10px', flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary)' }}>记忆金字塔</div>
          <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 3, maxWidth: 600, lineHeight: 1.6 }}>
            五个粒度共享一条时间轴：左粗右细（周→天→6时→时→5分），横向对齐就是父子关系。点任意方块看那段的流水账。
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <IconButton aria-label="前一天" onClick={() => { setDay(shiftDay(day, -1)); setSel(null) }} style={{ width: 30, height: 30 }}>
            <ChevronLeft size={16} />
          </IconButton>
          <div style={{ minWidth: 116, textAlign: 'center', fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>{day}</div>
          <IconButton aria-label="后一天" onClick={() => { setDay(shiftDay(day, 1)); setSel(null) }} style={{ width: 30, height: 30 }}>
            <ChevronRight size={16} />
          </IconButton>
          <Button onClick={() => { setDay(todayLocalLogicalDay()); setSel(null) }} style={{ padding: '5px 12px', fontSize: 12.5 }}>今天</Button>
        </div>
      </div>
      {/* Legend */}
      <div style={{ display: 'flex', gap: 12, padding: isMobile ? '0 14px 8px' : '0 22px 8px', flexWrap: 'wrap', fontSize: 11, color: 'var(--text-muted)' }}>
        {Object.entries(CAT_LABELS).filter(([k]) => k !== '_unclassified').map(([k, label]) => (
          <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 9, height: 9, borderRadius: 2, background: categoryColor(k) }} />{label}
          </span>
        ))}
        <span style={{ marginLeft: 'auto' }}>共 {total} 个窗 · 每 15 秒刷新</span>
      </div>

      {q.isError && (
        <div style={{ padding: isMobile ? '4px 14px 8px' : '4px 22px 8px' }}>
          <ErrorBanner title="加载失败" message="金字塔数据拉取失败，请稍后重试。" onRetry={() => q.refetch()} retrying={q.isRefetching} />
        </div>
      )}

      <div style={{ flex: 1, display: 'flex', minHeight: 0, gap: 0, position: 'relative' }}>
        {/* Chart: time axis + columns (scrolls vertically) */}
        <div style={{ flex: 1, overflowY: 'auto', overflowX: 'auto', padding: '4px 0 24px 0' }}>
          {q.isLoading ? (
            <SkeletonRows rows={10} height={52} style={{ padding: '24px 22px' }} />
          ) : (
            <div style={{ display: 'flex', minWidth: 'min-content', paddingLeft: 8 }}>
              <TimeAxis />
              {/* Column header band + lanes */}
              <div>
                <div
                  style={{
                    display: 'flex',
                    position: 'sticky',
                    top: 0,
                    zIndex: 2,
                    // Translucent + blur (same trick as the TopBar) so the sticky
                    // band doesn't paint a solid patch over the page glow.
                    background: 'color-mix(in srgb, var(--bg-base) 86%, transparent)',
                    backdropFilter: 'blur(8px)',
                    paddingBottom: 4,
                  }}
                >
                  {COLUMNS.map((c) => (
                    <div key={c.grain} style={{ width: c.width, textAlign: 'center', fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', letterSpacing: '0.04em' }}>{c.label}</div>
                  ))}
                </div>
                <div style={{ display: 'flex', position: 'relative', height: TOTAL_H }}>
                  {/* hour gridlines */}
                  {Array.from({ length: 25 }).map((_, h) => (
                    <div key={h} style={{ position: 'absolute', left: 0, right: 0, top: h * PX_PER_HOUR, borderTop: '1px solid var(--bg-border)', opacity: 0.5 }} />
                  ))}
                  {COLUMNS.map((c) => (
                    <Lane key={c.grain} width={c.width} windows={q.data?.grains?.[c.grain] ?? []} dayStart={dayStart} selKey={sel?.scope_key ?? null} onSelect={setSel} />
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Detail panel: desktop side column / mobile bottom sheet */}
        {isMobile ? (
          sel && (
            <>
              <div
                className="tt-fade"
                onClick={() => setSel(null)}
                style={{ position: 'fixed', inset: 0, background: 'var(--scrim)', zIndex: 40 }}
              />
              <div
                className="tt-sheet-content"
                data-state="open"
                style={{
                  position: 'fixed', left: 0, right: 0, bottom: 0, zIndex: 41,
                  maxHeight: '72vh',
                  background: 'var(--bg-surface)',
                  borderTop: '1px solid var(--bg-border)',
                  borderRadius: 'var(--radius-lg) var(--radius-lg) 0 0',
                  boxShadow: 'var(--shadow-lg)',
                  overflow: 'hidden',
                }}
              >
                <DetailPanel win={sel} onClose={() => setSel(null)} bare />
              </div>
            </>
          )
        ) : (
          <DetailPanel win={sel} onClose={() => setSel(null)} />
        )}
      </div>
    </div>
  )
}

function TimeAxis() {
  return (
    <div style={{ width: 48, flexShrink: 0, position: 'relative', marginTop: 22 /* header band */ }}>
      <div style={{ position: 'relative', height: TOTAL_H }}>
        {Array.from({ length: 25 }).map((_, h) => {
          const hour = (4 + h) % 24 // 4AM logical-day start
          return (
            <div key={h} style={{ position: 'absolute', top: h * PX_PER_HOUR - 6, right: 6, fontSize: 10.5, color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>
              {String(hour).padStart(2, '0')}:00
            </div>
          )
        })}
      </div>
    </div>
  )
}

function Lane({ width, windows, dayStart, selKey, onSelect }: {
  width: number; windows: SummaryWindow[]; dayStart: number; selKey: string | null
  onSelect: (w: SummaryWindow) => void
}) {
  return (
    <div style={{ width, position: 'relative', borderLeft: '1px solid var(--bg-border)' }}>
      {windows.map((w) => {
        const a = Math.max(0, Math.min(DAY_MS, w.window_start - dayStart))
        const b = Math.max(0, Math.min(DAY_MS, w.window_end - dayStart))
        const top = (a / DAY_MS) * TOTAL_H
        const height = Math.max(6, ((b - a) / DAY_MS) * TOTAL_H - 1.5)
        const cat = w.top_categories[0]?.category
        const color = cat ? categoryColor(cat) : 'var(--text-muted)'
        const selected = w.scope_key === selKey
        const narrated = w.status === 'narrated' && !w.metrics_only && !!w.description
        const tall = height >= 22
        return (
          <button
            key={w.scope_key}
            onClick={() => onSelect(w)}
            title={`${fmtHM(w.window_start)}–${fmtHM(w.window_end)} · ${w.record_count} 条${w.metrics_only ? ' · 仅指标' : narrated ? '' : ' · 待叙述'}`}
            style={{
              position: 'absolute', top, left: 3, right: 3, height,
              borderRadius: 4, cursor: 'pointer', overflow: 'hidden', textAlign: 'left',
              padding: tall ? '2px 5px' : 0,
              background: narrated ? color : 'transparent',
              border: narrated ? `1px solid ${color}` : `1px dashed ${color}`,
              opacity: narrated ? (selected ? 1 : 0.82) : 0.5,
              outline: selected ? '2px solid var(--accent)' : 'none',
              outlineOffset: 1,
              color: 'var(--accent-contrast)',
            }}
          >
            {tall && (
              <span style={{ fontSize: 10, fontWeight: 600, color: narrated ? 'var(--accent-contrast)' : 'var(--text-secondary)', whiteSpace: 'nowrap', textShadow: narrated ? '0 1px 2px rgba(0,0,0,0.3)' : 'none' }}>
                {fmtHM(w.window_start)}{cat ? ` ${CAT_LABELS[cat] ?? cat}` : ''}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

function DetailPanel({ win, onClose, bare = false }: { win: SummaryWindow | null; onClose: () => void; bare?: boolean }) {
  // bare: rendered inside the mobile bottom sheet (the sheet provides the
  // frame), otherwise the desktop side column provides it.
  const wrap: React.CSSProperties = bare
    ? { overflow: 'hidden' }
    : { width: 360, flexShrink: 0, borderLeft: '1px solid var(--bg-border)', background: 'var(--bg-surface)', overflow: 'hidden' }
  if (!win) {
    return (
      <div style={wrap}>
        <div style={{ padding: 24, color: 'var(--text-muted)', fontSize: 13, textAlign: 'center', marginTop: 40 }}>
          点左边任意方块，看那段时间的流水账、重点与评价。
        </div>
      </div>
    )
  }
  const grainLabel: Record<string, string> = { '5min': '5 分钟', '1h': '1 小时', '6h': '6 小时段', day: '一天', week: '一周' }
  return (
    <div style={wrap}>
      <div style={{ padding: '14px 16px', overflowY: 'auto', height: '100%' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--accent)' }}>{grainLabel[win.grain] ?? win.grain}</span>
          <IconButton aria-label="关闭详情" onClick={onClose} style={{ width: 28, height: 28, border: 'none', background: 'transparent' }}>
            <X size={15} />
          </IconButton>
        </div>
        <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', marginTop: 2, fontVariantNumeric: 'tabular-nums' }}>
          {fmtHM(win.window_start)} – {fmtHM(win.window_end)}
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 8, fontSize: 11.5, color: 'var(--text-muted)' }}>
          <span>{win.record_count} 条记录</span>
          <span>活跃 {Math.round(win.active_seconds / 60)} 分</span>
          {win.top_categories.slice(0, 3).map((c) => (
            <span key={c.category} style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
              <span style={{ width: 8, height: 8, borderRadius: 2, background: categoryColor(c.category) }} />
              {CAT_LABELS[c.category] ?? c.category} {Math.round(c.seconds / 60)}分
            </span>
          ))}
        </div>

        {win.metrics_only ? (
          <div style={{ marginTop: 14, padding: 12, borderRadius: 8, background: 'var(--bg-raised)', fontSize: 12.5, color: 'var(--text-muted)', lineHeight: 1.6 }}>
            这段无可叙述的视觉记录（多为窗口切换 / 无截图），只有指标。
          </div>
        ) : !win.description ? (
          <div style={{ marginTop: 14, padding: 12, borderRadius: 8, background: 'var(--bg-raised)', fontSize: 12.5, color: 'var(--text-muted)', lineHeight: 1.6 }}>
            待叙述（指标已建，叙述还没轮到 / 在等子窗）。
          </div>
        ) : (
          <>
            <Section title="流水账">
              <div style={{ fontSize: 13, lineHeight: 1.75, color: 'var(--text-primary)', whiteSpace: 'pre-wrap' }}>{win.description}</div>
            </Section>
            {win.key_points.length > 0 && (
              <Section title="重点">
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, lineHeight: 1.7, color: 'var(--text-secondary)' }}>
                  {win.key_points.map((k, i) => <li key={i}>{k}</li>)}
                </ul>
              </Section>
            )}
            {win.evaluation && (
              <Section title="评价">
                <div style={{ fontSize: 12.5, lineHeight: 1.7, color: 'var(--text-secondary)', fontStyle: 'italic' }}>{win.evaluation}</div>
              </Section>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', letterSpacing: '0.04em', marginBottom: 5 }}>{title}</div>
      {children}
    </div>
  )
}
