import { useState, useCallback, useEffect, useMemo } from 'react'
import { useRecords } from '@/hooks/useRecords'
import { TimelineCanvas } from '@/components/timeline/TimelineCanvas'
import { RecordDetailPanel } from '@/components/detail/RecordDetailPanel'
import { DatePicker } from '@/components/calendar/DatePicker'
import { ImageLightbox, type LightboxItem } from '@/components/lightbox/ImageLightbox'
import { toDateParam, fromDateParam } from '@/lib/dateUtils'

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

  const { data: records } = useRecords(selectedDate)

  const [lightboxIndex, setLightboxIndex] = useState<number>(-1)

  const lightboxItems = useMemo<LightboxItem[]>(
    () =>
      (records ?? []).map((r) => ({
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
    [records],
  )

  const handleDateChange = useCallback((date: Date) => {
    setSelectedDate(date)
    setSelectedRecordId(null)
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
    }
    window.addEventListener('popstate', handler)
    return () => window.removeEventListener('popstate', handler)
  }, [])

  return (
    <div style={{ display: 'flex', flex: 1, overflow: 'hidden', background: 'var(--bg-base)' }}>
      {/* 左侧日期选择器 */}
      <div style={{
        width: 220,
        borderRight: '1px solid var(--bg-border)',
        background: 'var(--bg-surface)',
        padding: '16px 12px',
        flexShrink: 0,
      }}>
        <DatePicker value={selectedDate} onChange={handleDateChange} />
      </div>

      {/* 中间时间轴 */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', padding: 16 }}>
        <TimelineCanvas
          records={records ?? []}
          date={selectedDate}
          selectedRecordId={selectedRecordId}
          onSelectRecord={handleSelectRecord}
          onGoToday={handleGoToday}
        />
      </div>

      {/* 右侧详情面板 */}
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
