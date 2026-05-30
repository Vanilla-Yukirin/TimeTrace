import { useState, useCallback, useEffect, useMemo } from 'react'
import { CalendarDays } from 'lucide-react'
import { format } from 'date-fns'
import { zhCN } from 'date-fns/locale'
import { useRecords } from '@/hooks/useRecords'
import { TimelineCanvas } from '@/components/timeline/TimelineCanvas'
import { CategoryFilter, UNCATEGORIZED } from '@/components/timeline/CategoryFilter'
import { RecordDetailPanel } from '@/components/detail/RecordDetailPanel'
import { DatePicker } from '@/components/calendar/DatePicker'
import { ImageLightbox, type LightboxItem } from '@/components/lightbox/ImageLightbox'
import { toDateParam, fromDateParam, formatDurationMs } from '@/lib/dateUtils'

export function TimelinePage() {
  const [selectedDate, setSelectedDate] = useState(() => {
    const params = new URLSearchParams(window.location.search)
    const dateStr = params.get('date')
    return dateStr ? fromDateParam(dateStr) : new Date()
  })

  const [selectedRecordId, setSelectedRecordId] = useState<string | null>(() => {
    const params = new URLSearchParams(window.location.search)
    return params.get('highlight')
  })

  // null = 全部; otherwise a category_final value (or UNCATEGORIZED sentinel).
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null)

  const { data: records } = useRecords(selectedDate)
  const allRecords = useMemo(() => records ?? [], [records])

  const shownRecords = useMemo(() => {
    if (categoryFilter === null) return allRecords
    if (categoryFilter === UNCATEGORIZED) return allRecords.filter((r) => r.category_final == null)
    return allRecords.filter((r) => r.category_final === categoryFilter)
  }, [allRecords, categoryFilter])

  const [lightboxIndex, setLightboxIndex] = useState<number>(-1)

  const lightboxItems = useMemo<LightboxItem[]>(
    () =>
      shownRecords.map((r) => ({
        id: r.id,
        thumbPath: r.thumb_path,
        tsStart: r.ts_start,
        tsEnd: r.ts_end,
        appName: r.app_name,
        windowTitle: r.window_title,
        url: r.url,
        categoryFinal: r.category_final,
        categoryConfidence: r.confidence,
        vlmDesc: r.vlm_desc,
      })),
    [shownRecords],
  )

  // Day summary: count + total tracked time across the shown records.
  const totalMs = useMemo(
    () => shownRecords.reduce((s, r) => s + ((r.ts_end ?? Date.now()) - r.ts_start), 0),
    [shownRecords],
  )

  const handleDateChange = useCallback((date: Date) => {
    setSelectedDate(date)
    setSelectedRecordId(null)
    setCategoryFilter(null)
    setLightboxIndex(-1)
    const params = new URLSearchParams(window.location.search)
    params.set('date', toDateParam(date))
    window.history.pushState({}, '', `${window.location.pathname}?${params.toString()}`)
  }, [])

  const handleSelectRecord = useCallback((id: string | null) => {
    setSelectedRecordId(id)
  }, [])

  const handleZoom = useCallback(
    (recordId: string) => {
      const idx = lightboxItems.findIndex((it) => it.id === recordId)
      if (idx >= 0) setLightboxIndex(idx)
    },
    [lightboxItems],
  )

  const handleLightboxIndexChange = useCallback(
    (next: number) => {
      setLightboxIndex(next)
      const item = lightboxItems[next]
      if (item) setSelectedRecordId(item.id)
    },
    [lightboxItems],
  )

  const handleLightboxOpenChange = useCallback((open: boolean) => {
    if (!open) setLightboxIndex(-1)
  }, [])

  const handleGoToday = useCallback(() => {
    handleDateChange(new Date())
  }, [handleDateChange])

  useEffect(() => {
    const handler = () => {
      const params = new URLSearchParams(window.location.search)
      const dateStr = params.get('date')
      setSelectedDate(dateStr ? fromDateParam(dateStr) : new Date())
      setSelectedRecordId(params.get('highlight'))
      setCategoryFilter(null)
    }
    window.addEventListener('popstate', handler)
    return () => window.removeEventListener('popstate', handler)
  }, [])

  return (
    <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
      {/* Left: calendar + quick filters */}
      <aside
        style={{
          width: 224,
          borderRight: '1px solid var(--bg-border)',
          background: 'var(--bg-surface)',
          padding: '16px 14px',
          flexShrink: 0,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 18,
        }}
      >
        <DatePicker value={selectedDate} onChange={handleDateChange} />
        <div style={{ height: 1, background: 'var(--bg-border)' }} />
        <CategoryFilter
          records={allRecords}
          selected={categoryFilter}
          onSelect={setCategoryFilter}
        />
      </aside>

      {/* Center: header + timeline */}
      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          padding: '18px 22px 60px',
          minWidth: 0,
          overflowY: 'auto',
        }}
      >
        <TimelineHeader
          date={selectedDate}
          count={shownRecords.length}
          totalMs={totalMs}
          onToday={handleGoToday}
        />
        <div style={{ marginTop: 18 }}>
          <TimelineCanvas
            records={shownRecords}
            date={selectedDate}
            selectedRecordId={selectedRecordId}
            onSelectRecord={handleSelectRecord}
            onGoToday={handleGoToday}
          />
        </div>
      </div>

      {/* Right: detail panel */}
      <RecordDetailPanel
        recordId={selectedRecordId}
        onClose={() => setSelectedRecordId(null)}
        onZoom={handleZoom}
      />

      <ImageLightbox
        items={lightboxItems}
        index={lightboxIndex >= 0 ? Math.min(lightboxIndex, lightboxItems.length - 1) : 0}
        onIndexChange={handleLightboxIndexChange}
        open={lightboxIndex >= 0 && lightboxItems.length > 0}
        onOpenChange={handleLightboxOpenChange}
      />
    </div>
  )
}

function TimelineHeader({
  date,
  count,
  totalMs,
  onToday,
}: {
  date: Date
  count: number
  totalMs: number
  onToday: () => void
}) {
  const isToday = format(date, 'yyyy-MM-dd') === format(new Date(), 'yyyy-MM-dd')
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12 }}>
      <div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, color: 'var(--text-primary)' }}>
            {format(date, 'M月d日', { locale: zhCN })}
          </h1>
          <span style={{ fontSize: 14, color: 'var(--text-secondary)', fontWeight: 500 }}>
            {format(date, 'EEEE', { locale: zhCN })}
          </span>
          {isToday && (
            <span
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: 'var(--accent)',
                background: 'var(--accent-subtle)',
                padding: '2px 8px',
                borderRadius: 'var(--radius-pill)',
              }}
            >
              今天
            </span>
          )}
        </div>
        <div style={{ marginTop: 5, fontSize: 12.5, color: 'var(--text-muted)' }}>
          {count} 条活动
          {totalMs > 0 && <> · 共记录 {formatDurationMs(totalMs)}</>}
        </div>
      </div>

      {!isToday && (
        <button
          onClick={onToday}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            padding: '7px 13px',
            borderRadius: 'var(--radius-md)',
            background: 'var(--bg-surface)',
            border: '1px solid var(--bg-border)',
            color: 'var(--text-secondary)',
            fontSize: 13,
            fontWeight: 500,
            cursor: 'pointer',
            flexShrink: 0,
          }}
        >
          <CalendarDays size={14} />
          回到今天
        </button>
      )}
    </div>
  )
}
