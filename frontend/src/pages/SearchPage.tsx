import { useCallback, useEffect, useMemo, useState } from 'react'
import { Search as SearchIcon } from 'lucide-react'
import { CatMascot } from '@/components/brand/CatMascot'
import { ImageDropzone } from '@/components/search/ImageDropzone'
import { FilterPanel } from '@/components/search/FilterPanel'
import { ResultRow } from '@/components/search/ResultRow'
import { ImageLightbox, type LightboxItem } from '@/components/lightbox/ImageLightbox'
import { useSearchQuery, type SearchParams } from '@/hooks/useSearchQuery'
import { fromDateParam } from '@/lib/dateUtils'

export function SearchPage() {
  // Pending inputs (controlled by the user; not yet submitted)
  const [q, setQ] = useState('')
  const [images, setImages] = useState<File[]>([])
  const [visual, setVisual] = useState(true)
  const [semantic, setSemantic] = useState(true)
  const [startDate, setStartDate] = useState<string | null>(null)
  const [endDate, setEndDate] = useState<string | null>(null)
  const [apps, setApps] = useState<string[]>([])
  const [categories, setCategories] = useState<string[]>([])

  // Committed params (only changes on Submit — avoids querying on every keystroke)
  const [submitted, setSubmitted] = useState<SearchParams | null>(null)

  const { data, isFetching, error } = useSearchQuery(submitted)

  const [lightboxIndex, setLightboxIndex] = useState<number>(-1)

  const lightboxItems = useMemo<LightboxItem[]>(
    () =>
      (data?.items ?? []).map((it) => ({
        id: it.screenshot_id,
        thumbPath: it.thumb_path,
        tsStart: it.ts_start,
        tsEnd: it.ts_end,
        appName: it.app_name,
        windowTitle: it.window_title,
        url: it.url,
        categoryFinal: it.category_final,
        categoryConfidence: null,
        vlmDesc: it.vlm_desc,
      })),
    [data],
  )

  const handleZoom = useCallback((idx: number) => setLightboxIndex(idx), [])
  const handleLightboxOpenChange = useCallback((open: boolean) => {
    if (!open) setLightboxIndex(-1)
  }, [])

  // Close lightbox when a new search is submitted — old indices may collide
  // with the new result set, and the user's frame of reference has shifted.
  useEffect(() => {
    setLightboxIndex(-1)
  }, [submitted])

  const hasAnyInput = useMemo(
    () =>
      q.trim().length > 0 ||
      images.length > 0 ||
      apps.length > 0 ||
      categories.length > 0 ||
      startDate !== null ||
      endDate !== null,
    [q, images, apps, categories, startDate, endDate],
  )

  const submit = () => {
    if (!hasAnyInput) return
    const dayMs = 86_400_000
    const start = startDate ? fromDateParam(startDate).getTime() : null
    const end = endDate ? fromDateParam(endDate).getTime() + dayMs - 1 : null
    setSubmitted({
      q: q.trim(),
      images,
      visual,
      semantic,
      radius: 10,
      start,
      end,
      apps,
      categories,
      limit: 50,
    })
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        flex: 1,
        overflow: 'hidden',
        background: 'transparent',
      }}
    >
      {/* Scrollable body */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '20px 24px',
          maxWidth: 960,
          width: '100%',
          margin: '0 auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
        }}
      >
        {/* Keyword + submit */}
        <form
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
          style={{ display: 'flex', gap: 8 }}
        >
          <div
            style={{
              flex: 1,
              display: 'flex',
              alignItems: 'center',
              gap: 9,
              padding: '0 14px',
              height: 44,
              background: 'var(--bg-surface)',
              border: '1px solid var(--bg-border)',
              borderRadius: 'var(--radius-lg)',
              boxShadow: 'var(--shadow-sm)',
            }}
          >
            <SearchIcon size={17} style={{ color: 'var(--text-muted)' }} />
            <input
              autoFocus
              aria-label="搜索关键词"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="关键词（标题/应用/进程/URL/画面描述都搜）。中文 ≥3 字走 FTS5 BM25 排序"
              style={{
                flex: 1,
                background: 'transparent',
                border: 'none',
                color: 'var(--text-primary)',
                fontSize: 14,
                outline: 'none',
              }}
            />
          </div>
          <button
            type="submit"
            disabled={!hasAnyInput || isFetching}
            style={{
              padding: '0 20px',
              height: 44,
              background: hasAnyInput ? 'var(--grad-accent)' : 'var(--bg-raised)',
              color: hasAnyInput ? '#fff' : 'var(--text-muted)',
              border: 'none',
              borderRadius: 'var(--radius-lg)',
              cursor: hasAnyInput ? 'pointer' : 'not-allowed',
              fontSize: 14,
              fontWeight: 600,
              boxShadow: hasAnyInput ? 'var(--shadow-glow)' : 'none',
            }}
          >
            {isFetching ? '搜索中…' : '搜索'}
          </button>
        </form>

        {/* Image dropzone */}
        <ImageDropzone images={images} onChange={setImages} />

        {/* Mode toggles (only when images present) */}
        {images.length > 0 && (
          <div
            style={{
              display: 'flex',
              gap: 16,
              padding: '12px 14px',
              background: 'var(--bg-surface)',
              border: '1px solid var(--bg-border)',
              borderRadius: 'var(--radius-lg)',
            }}
          >
            <ModeCheckbox
              checked={visual}
              onChange={setVisual}
              label="视觉相似"
              hint="pHash · 画面一致"
            />
            <ModeCheckbox
              checked={semantic}
              onChange={setSemantic}
              label="语义相似"
              hint="VLM + BM25 · 内容一致"
              warning={
                data?.semanticChannel === 'unavailable' ? '需 VLM，当前未配置' : undefined
              }
            />
          </div>
        )}

        {/* Filters */}
        <FilterPanel
          startDate={startDate}
          endDate={endDate}
          apps={apps}
          categories={categories}
          onStartDate={setStartDate}
          onEndDate={setEndDate}
          onApps={setApps}
          onCategories={setCategories}
        />

        {/* Results */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
          {submitted === null && (
            <div
              style={{
                marginTop: 28,
                padding: 32,
                textAlign: 'center',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                gap: 6,
              }}
            >
              <CatMascot size={84} float />
              <div style={{ fontSize: 14, color: 'var(--text-secondary)', fontWeight: 500, marginTop: 6 }}>
                多模态搜索
              </div>
              <div style={{ fontSize: 12.5, color: 'var(--text-muted)', maxWidth: 360, lineHeight: 1.6 }}>
                输入关键词、上传参考图，或选择筛选条件后点「搜索」。
                支持关键词 · 以图搜图 · VLM 语义。
              </div>
            </div>
          )}
          {error && (
            <EmptyState text={`搜索失败：${(error as Error).message}`} error />
          )}
          {submitted && !error && data && data.items.length === 0 && !isFetching && (
            <EmptyState
              text={
                q.trim().length === 1
                  ? '关键词太短（仅 1 字符）。试试 2-3 字以上的具体词。'
                  : '未找到符合条件的活动。中文搜索建议 ≥3 字（走 FTS5），或换个应用名/进程名/URL 关键词试试。'
              }
            />
          )}
          {data && data.items.length > 0 && (
            <>
              <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: '4px 2px' }}>
                共 {data.total} 条
                {data.source === 'image' && (
                  <>
                    {' · '}视觉通道：{channelLabel(data.visualChannel)}
                    {' · '}语义通道：{channelLabel(data.semanticChannel)}
                  </>
                )}
              </div>
              {data.items.map((it, idx) => (
                <ResultRow key={it.screenshot_id} item={it} onZoom={() => handleZoom(idx)} />
              ))}
            </>
          )}
        </div>
      </div>

      <ImageLightbox
        items={lightboxItems}
        index={lightboxIndex >= 0 ? Math.min(lightboxIndex, lightboxItems.length - 1) : 0}
        onIndexChange={setLightboxIndex}
        open={lightboxIndex >= 0 && lightboxItems.length > 0}
        onOpenChange={handleLightboxOpenChange}
      />
    </div>
  )
}

function ModeCheckbox({
  checked,
  onChange,
  label,
  hint,
  warning,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label: string
  hint: string
  warning?: string
}) {
  return (
    <label style={{ display: 'flex', alignItems: 'flex-start', gap: 6, cursor: 'pointer' }}>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        style={{ marginTop: 3 }}
      />
      <div>
        <div style={{ fontSize: 13, color: 'var(--text-primary)', fontWeight: 500 }}>{label}</div>
        <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{hint}</div>
        {warning && (
          <div style={{ fontSize: 10, color: 'var(--warning)', marginTop: 2 }}>⚠ {warning}</div>
        )}
      </div>
    </label>
  )
}

function EmptyState({ text, error = false }: { text: string; error?: boolean }) {
  return (
    <div
      style={{
        padding: 32,
        textAlign: 'center',
        color: error ? 'var(--error)' : 'var(--text-muted)',
        fontSize: 13,
        border: '1px dashed var(--bg-border)',
        borderRadius: 'var(--radius-lg)',
        marginTop: 20,
      }}
    >
      {text}
    </div>
  )
}

function channelLabel(status: string): string {
  if (status === 'ok') return '已启用'
  if (status === 'unavailable') return '不可用'
  return '未启用'
}
