import { useState, useCallback, useEffect } from 'react'
import { useRecords } from '@/hooks/useRecords'
import { TimelineCanvas } from '@/components/timeline/TimelineCanvas'
import { RecordDetailPanel } from '@/components/detail/RecordDetailPanel'
import { DatePicker } from '@/components/calendar/DatePicker'
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

  const handleDateChange = useCallback((date: Date) => {
    setSelectedDate(date)
    setSelectedRecordId(null)
    const params = new URLSearchParams(window.location.search)
    params.set('date', toDateParam(date))
    window.history.pushState({}, '', `${window.location.pathname}?${params.toString()}`)
  }, [])

  const handleSelectRecord = useCallback((id: string | null) => {
    setSelectedRecordId(id)
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
      />
    </div>
  )
}
