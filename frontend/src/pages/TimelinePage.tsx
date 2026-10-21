import { useState, useCallback, useEffect, useMemo } from 'react'
import { CalendarDays, SlidersHorizontal } from 'lucide-react'
import { format } from 'date-fns'
import { zhCN } from 'date-fns/locale'
import { useIsMobile } from '@/hooks/useIsMobile'
import { useRecords } from '@/hooks/useRecords'
import { deviceKeyOf, useDeviceDirectory } from '@/hooks/useDevices'
import { DeviceSelect } from '@/components/devices/DeviceSelect'
import { useNow } from '@/hooks/useNow'
import { TimelineCanvas } from '@/components/timeline/TimelineCanvas'
import { CategoryFilter, UNCATEGORIZED } from '@/components/timeline/CategoryFilter'
import { RecordDetailPanel } from '@/components/detail/RecordDetailPanel'
import { DatePicker } from '@/components/calendar/DatePicker'
import { ImageLightbox, type LightboxItem } from '@/components/lightbox/ImageLightbox'
import { Button } from '@/components/ui/Button'
import { Divider } from '@/components/ui/PageShell'
import { ErrorBanner } from '@/components/ui/Feedback'
import { Skeleton } from '@/components/ui/Skeleton'
import { toDateParam, fromDateParam, formatDurationMs } from '@/lib/dateUtils'
import type { ApiRecord } from '@/types/api'

/** Patch the current URL's query (null deletes a key). Date changes push a
 *  history entry; selection / filter tweaks replace it so back isn't spammed. */
function patchUrl(patch: Record<string, string | null>, push = false): void {
  const params = new URLSearchParams(window.location.search)
  for (const [k, v] of Object.entries(patch)) {
    if (v == null) params.delete(k)
    else params.set(k, v)
  }
  const qs = params.toString()
  const url = `${window.location.pathname}${qs ? `?${qs}` : ''}`
  if (push) window.history.pushState({}, '', url)
  else window.history.replaceState(window.history.state, '', url)
}

function urlParam(key: string): string | null {
  return new URLSearchParams(window.location.search).get(key)
}

/** Focus is in something that owns arrow keys (inputs, selects, editors). */
function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false
  const tag = el.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable
}

export function TimelinePage() {
  const [selectedDate, setSelectedDate] = useState(() => {
    const dateStr = urlParam('date')
    return dateStr ? fromDateParam(dateStr) : new Date()
  })

  const [selectedRecordId, setSelectedRecordId] = useState<string | null>(() => urlParam('highlight'))

  // null = 全部; otherwise a category_final value (or UNCATEGORIZED sentinel).
  const [categoryFilter, setCategoryFilter] = useState<string | null>(() => urlParam('cat'))
  // null = all devices (one lane per device); uuid / _unknown_device = one device.
  const [deviceFilter, setDeviceFilter] = useState<string | null>(() => urlParam('device'))
  const devices = useDeviceDirectory()

  // Mirror selection / filters into the URL (replace, not push).
  useEffect(() => {
    patchUrl({ highlight: selectedRecordId, cat: categoryFilter, device: deviceFilter })
  }, [selectedRecordId, categoryFilter, deviceFilter])

  const isMobile = useIsMobile()
  // Mobile only: the calendar + quick-filters live in a collapsible panel.
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false)

  const { data: records, isLoading, isError, error, refetch, isRefetching } = useRecords(selectedDate, deviceFilter)
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
        imagePath: r.image_path,
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

  // Day summary: wall-clock union across devices (parallel use counts once)
  // plus the plain per-record sum, which exceeds it when devices overlap.
  const durationSummary = useMemo(() => summarizeDurations(shownRecords), [shownRecords])

  // Records in time order (all lanes) for keyboard left/right navigation.
  const navOrder = useMemo(
    () => [...shownRecords].sort((a, b) => a.ts_start - b.ts_start || a.id.localeCompare(b.id)),
    [shownRecords],
  )

  const handleDateChange = useCallback((date: Date) => {
    setSelectedDate(date)
    setSelectedRecordId(null)
    // The category filter is kept across days (it lives in the URL too).
    setLightboxIndex(-1)
    patchUrl({ date: toDateParam(date), highlight: null }, true)
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
      const dateStr = urlParam('date')
      setSelectedDate(dateStr ? fromDateParam(dateStr) : new Date())
      setSelectedRecordId(urlParam('highlight'))
      setCategoryFilter(urlParam('cat'))
      setDeviceFilter(urlParam('device'))
    }
    window.addEventListener('popstate', handler)
    return () => window.removeEventListener('popstate', handler)
  }, [])

  // Keyboard: left/right = previous/next record in time order (across device
  // lanes, respecting filters); Esc clears the selection. Ignored while typing
  // and while the lightbox (which owns the arrows) is open.
  const lightboxOpen = lightboxIndex >= 0
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (lightboxOpen || e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return
      if (isTypingTarget(e.target)) return
      if (e.key === 'Escape') {
        setSelectedRecordId(null)
        return
      }
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
      if (navOrder.length === 0) return
      e.preventDefault()
      setSelectedRecordId((cur) => {
        const idx = cur ? navOrder.findIndex((r) => r.id === cur) : -1
        if (idx < 0) return (e.key === 'ArrowLeft' ? navOrder[navOrder.length - 1] : navOrder[0]).id
        const next = e.key === 'ArrowLeft' ? Math.max(0, idx - 1) : Math.min(navOrder.length - 1, idx + 1)
        return navOrder[next].id
      })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navOrder, lightboxOpen])

  const filtersPanel = (
    <>
      <DatePicker value={selectedDate} onChange={handleDateChange} />
      <Divider />
      <DeviceSelect value={deviceFilter} onChange={setDeviceFilter} />
      <CategoryFilter records={allRecords} selected={categoryFilter} onSelect={setCategoryFilter} />
    </>
  )

  const detail = (
    <RecordDetailPanel
      isMobile={isMobile}
      recordId={selectedRecordId}
      onClose={() => setSelectedRecordId(null)}
      onZoom={handleZoom}
    />
  )

  const lightbox = (
    <ImageLightbox
      items={lightboxItems}
      index={lightboxIndex >= 0 ? Math.min(lightboxIndex, lightboxItems.length - 1) : 0}
      onIndexChange={handleLightboxIndexChange}
      open={lightboxIndex >= 0 && lightboxItems.length > 0}
      onOpenChange={handleLightboxOpenChange}
    />
  )

  // Loading and failure must look different from "an empty day" — previously
  // all three rendered as a blank canvas.
  const canvasArea = isError ? (
    <ErrorBanner
      title="时间轴加载失败"
      message={String((error as Error)?.message ?? error)}
      onRetry={() => refetch()}
      retrying={isRefetching}
    />
  ) : isLoading ? (
    <Skeleton style={{ height: 340, borderRadius: 'var(--radius-lg)' }} />
  ) : (
    <TimelineCanvas
      records={shownRecords}
      date={selectedDate}
      selectedRecordId={selectedRecordId}
      onSelectRecord={handleSelectRecord}
      deviceOrder={devices.order}
      deviceLabels={devices.labels}
    />
  )

  // ---- Mobile: single column, filters collapsible, detail as overlay ----
  if (isMobile) {
    return (
      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          padding: '14px 14px 48px',
          minWidth: 0,
          overflowY: 'auto',
        }}
      >
        <TimelineHeader
          date={selectedDate}
          count={shownRecords.length}
          summary={durationSummary}
          onToday={handleGoToday}
          isMobile
          filtersOpen={mobileFiltersOpen}
          onToggleFilters={() => setMobileFiltersOpen((o) => !o)}
        />
        {mobileFiltersOpen && (
          <div
            className="tt-fade"
            style={{
              marginTop: 12,
              padding: 14,
              background: 'var(--bg-surface)',
              border: '1px solid var(--bg-border)',
              borderRadius: 'var(--radius-lg)',
              display: 'flex',
              flexDirection: 'column',
              gap: 16,
            }}
          >
            {filtersPanel}
          </div>
        )}
        <div style={{ marginTop: 16 }}>{canvasArea}</div>
        {detail}
        {lightbox}
      </div>
    )
  }

  // ---- Desktop: three columns ----
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
        {filtersPanel}
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
          summary={durationSummary}
          onToday={handleGoToday}
        />
        <div style={{ marginTop: 18 }}>{canvasArea}</div>
      </div>

      {/* Right: detail panel */}
      {detail}

      {lightbox}
    </div>
  )
}

interface DurationSummary {
  /** Sum of closed spans (per-device sum, double-counts parallel use). */
  closedTotal: number
  activeCount: number
  activeStartTotal: number
  /** Wall-clock union of closed spans, clipped before the first open record. */
  unionBeforeOpen: number
  /** Earliest still-open record's start; it runs to "now" (null = none open). */
  firstOpenStart: number | null
  /** Records come from more than one device. */
  multiDevice: boolean
}

/** One pass over the day's records. Open records all run to "now", so their
 *  union is simply [earliest open start, now]; closed spans contribute their
 *  union up to that point. */
function summarizeDurations(records: ApiRecord[]): DurationSummary {
  let closedTotal = 0
  let activeCount = 0
  let activeStartTotal = 0
  let firstOpenStart: number | null = null
  const devices = new Set<string>()
  const closed: [number, number][] = []
  for (const r of records) {
    devices.add(deviceKeyOf(r))
    if (r.ts_end == null) {
      activeCount += 1
      activeStartTotal += r.ts_start
      firstOpenStart = firstOpenStart == null ? r.ts_start : Math.min(firstOpenStart, r.ts_start)
    } else if (r.ts_end > r.ts_start) {
      closedTotal += r.ts_end - r.ts_start
      closed.push([r.ts_start, r.ts_end])
    }
  }
  closed.sort((a, b) => a[0] - b[0])
  const limit = firstOpenStart ?? Infinity
  let unionBeforeOpen = 0
  let curS = -Infinity
  let curE = -Infinity
  for (const [s, e0] of closed) {
    if (s >= limit) break
    const e = Math.min(e0, limit)
    if (s > curE) {
      if (curE > curS) unionBeforeOpen += curE - curS
      curS = s
      curE = e
    } else if (e > curE) {
      curE = e
    }
  }
  if (curE > curS) unionBeforeOpen += curE - curS
  return {
    closedTotal,
    activeCount,
    activeStartTotal,
    unionBeforeOpen,
    firstOpenStart,
    multiDevice: devices.size > 1,
  }
}

/** The only part of the page that depends on the wall clock: isolating the 1s
 *  tick here keeps it from re-rendering the whole page (and canvas) each second. */
function TrackedTotal({ summary }: { summary: DurationSummary }) {
  const now = useNow(summary.activeCount > 0)
  const openMs =
    summary.firstOpenStart != null && now != null ? Math.max(0, now - summary.firstOpenStart) : 0
  const wallMs = summary.unionBeforeOpen + openMs
  const sumMs =
    summary.closedTotal + (now == null ? 0 : summary.activeCount * now - summary.activeStartTotal)
  if (wallMs <= 0) return null
  return (
    <>
      {' · '}共记录 {formatDurationMs(wallMs)}
      {summary.multiDevice && sumMs - wallMs >= 60_000 && (
        <span title="多台设备同时使用的时段按每台分别计入" style={{ opacity: 0.75 }}>
          （各设备合计 {formatDurationMs(sumMs)}）
        </span>
      )}
    </>
  )
}

function TimelineHeader({
  date,
  count,
  summary,
  onToday,
  isMobile = false,
  filtersOpen = false,
  onToggleFilters,
}: {
  date: Date
  count: number
  summary: DurationSummary
  onToday: () => void
  isMobile?: boolean
  filtersOpen?: boolean
  onToggleFilters?: () => void
}) {
  const isToday = format(date, 'yyyy-MM-dd') === format(new Date(), 'yyyy-MM-dd')
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12 }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
          <h1 style={{ margin: 0, fontSize: isMobile ? 20 : 22, fontWeight: 800, color: 'var(--text-primary)' }}>
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
          <TrackedTotal summary={summary} />
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
        {isMobile && onToggleFilters && (
          <Button
            onClick={onToggleFilters}
            active={filtersOpen}
            aria-expanded={filtersOpen}
            aria-label="日期与筛选"
            style={{ minWidth: 44, height: 38, padding: '0 12px' }}
          >
            <SlidersHorizontal size={15} aria-hidden="true" />
            日期
          </Button>
        )}
        {!isToday && (
          <Button
            onClick={onToday}
            style={{ minHeight: isMobile ? 38 : undefined }}
          >
            <CalendarDays size={14} aria-hidden="true" />
            {isMobile ? '今天' : '回到今天'}
          </Button>
        )}
      </div>
    </div>
  )
}
