import { useCallback, useEffect, useMemo, useState } from 'react'
import { Search as SearchIcon } from 'lucide-react'
import { useIsMobile } from '@/hooks/useIsMobile'
import { CatMascot } from '@/components/brand/CatMascot'
import { ImageDropzone } from '@/components/search/ImageDropzone'
import { FilterPanel } from '@/components/search/FilterPanel'
import { ResultRow } from '@/components/search/ResultRow'
import { ImageLightbox, type LightboxItem } from '@/components/lightbox/ImageLightbox'
import { Button } from '@/components/ui/Button'
import { ErrorBanner } from '@/components/ui/Feedback'
import { SkeletonRows } from '@/components/ui/Skeleton'
import { useSearchQuery, type SearchParams } from '@/hooks/useSearchQuery'
import { fromDateParam } from '@/lib/dateUtils'

/** Text inputs of the search form. Everything except reference images is
 *  mirrored into the URL so a search survives refresh / back / sharing. */
interface SearchInputs {
  q: string
  visual: boolean
  semantic: boolean
  startDate: string | null
  endDate: string | null
  apps: string[]
  categories: string[]
  device: string | null
}

function csv(v: string | null): string[] {
  return v ? v.split(',').filter(Boolean) : []
}

function readSearchUrl(): { inputs: SearchInputs; active: boolean } {
  const sp = new URLSearchParams(window.location.search)
  const inputs: SearchInputs = {
    q: sp.get('q') ?? '',
    visual: sp.get('visual') !== '0',
    semantic: sp.get('semantic') !== '0',
    startDate: sp.get('from'),
    endDate: sp.get('to'),
    apps: csv(sp.get('apps')),
    categories: csv(sp.get('cats')),
    device: sp.get('device'),
  }
  const active = ['q', 'from', 'to', 'apps', 'cats', 'device'].some((k) => sp.get(k))
  return { inputs, active }
}

function writeSearchUrl(i: SearchInputs): void {
  const sp = new URLSearchParams()
  if (i.q) sp.set('q', i.q)
  if (i.startDate) sp.set('from', i.startDate)
  if (i.endDate) sp.set('to', i.endDate)
  if (i.apps.length) sp.set('apps', i.apps.join(','))
  if (i.categories.length) sp.set('cats', i.categories.join(','))
  if (i.device) sp.set('device', i.device)
  if (!i.visual) sp.set('visual', '0')
  if (!i.semantic) sp.set('semantic', '0')
  const qs = sp.toString()
  const url = `${window.location.pathname}${qs ? `?${qs}` : ''}`
  if (url !== `${window.location.pathname}${window.location.search}`) {
    window.history.pushState({}, '', url)
  }
}

function toParams(i: SearchInputs, images: File[]): SearchParams {
  const dayMs = 86_400_000
  return {
    q: i.q.trim(),
    images,
    visual: i.visual,
    semantic: i.semantic,
    radius: 10,
    start: i.startDate ? fromDateParam(i.startDate).getTime() : null,
    end: i.endDate ? fromDateParam(i.endDate).getTime() + dayMs - 1 : null,
    apps: i.apps,
    categories: i.categories,
    device: i.device,
    limit: 50,
  }
}

export function SearchPage() {
  const [initial] = useState(readSearchUrl)
  // Pending inputs (controlled by the user; not yet submitted)
  const [q, setQ] = useState(initial.inputs.q)
  const [images, setImages] = useState<File[]>([])
  const [visual, setVisual] = useState(initial.inputs.visual)
  const [semantic, setSemantic] = useState(initial.inputs.semantic)
  const [startDate, setStartDate] = useState<string | null>(initial.inputs.startDate)
  const [endDate, setEndDate] = useState<string | null>(initial.inputs.endDate)
  const [apps, setApps] = useState<string[]>(initial.inputs.apps)
  const [categories, setCategories] = useState<string[]>(initial.inputs.categories)
  const [device, setDevice] = useState<string | null>(initial.inputs.device)

  // Committed params (only changes on Submit — avoids querying on every keystroke).
  // A URL carrying a search (refresh / shared link / back) runs it right away.
  const [submitted, setSubmitted] = useState<SearchParams | null>(() =>
    initial.active ? toParams(initial.inputs, []) : null,
  )
  const isMobile = useIsMobile()

  // Back / forward: restore the form from the URL and re-run that search.
  useEffect(() => {
    const onPop = () => {
      const { inputs, active } = readSearchUrl()
      setQ(inputs.q)
      setVisual(inputs.visual)
      setSemantic(inputs.semantic)
      setStartDate(inputs.startDate)
      setEndDate(inputs.endDate)
      setApps(inputs.apps)
      setCategories(inputs.categories)
      setDevice(inputs.device)
      setImages([])
      setSubmitted(active ? toParams(inputs, []) : null)
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

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

  const hasAnyInput = useMemo(
    () =>
      q.trim().length > 0 ||
      images.length > 0 ||
      apps.length > 0 ||
      categories.length > 0 ||
      startDate !== null ||
      endDate !== null ||
      device !== null,
    [q, images, apps, categories, startDate, endDate, device],
  )

  const submit = () => {
    if (!hasAnyInput) return
    // Old indices may collide with the next result set.
    setLightboxIndex(-1)
    const inputs: SearchInputs = { q: q.trim(), visual, semantic, startDate, endDate, apps, categories, device }
    writeSearchUrl(inputs)
    setSubmitted(toParams(inputs, images))
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
          padding: isMobile ? '16px 14px 32px' : '20px 24px',
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
              placeholder="关键词或一句描述（标题/应用/URL/画面描述 + 语义相似一起搜）"
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
          <Button
            type="submit"
            variant="primary"
            disabled={!hasAnyInput}
            loading={isFetching}
            style={{ padding: '0 20px', height: 44, borderRadius: 'var(--radius-lg)', fontSize: 14 }}
          >
            {isFetching ? '搜索中…' : '搜索'}
          </Button>
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
              hint="VLM 描述 + 语义检索 · 内容一致"
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
          device={device}
          onDevice={setDevice}
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
            <ErrorBanner
              style={{ marginTop: 20 }}
              title="搜索失败"
              message={(error as Error).message}
            />
          )}
          {submitted && !error && isFetching && !data && (
            <SkeletonRows rows={3} height={78} gap={8} style={{ marginTop: 12 }} />
          )}
          {submitted && !error && data && data.items.length === 0 && !isFetching && (
            <div
              style={{
                padding: 32,
                textAlign: 'center',
                color: 'var(--text-muted)',
                fontSize: 13,
                border: '1px dashed var(--bg-border)',
                borderRadius: 'var(--radius-lg)',
                marginTop: 20,
              }}
            >
              {q.trim().length === 1
                ? '关键词太短（仅 1 字符）。试试 2-3 字以上的具体词。'
                : '未找到符合条件的活动。中文搜索建议 ≥3 字（走 FTS5），或换个应用名/进程名/URL 关键词试试。'}
            </div>
          )}
          {data && data.items.length > 0 && (
            <>
              <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: '4px 2px' }}>
                共 {data.total} 条
                {data.source === 'text' && (
                  <>
                    {' · '}语义通道：{channelLabel(data.semanticChannel)}
                  </>
                )}
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
    <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, cursor: 'pointer' }}>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        style={{ marginTop: 2, width: 15, height: 15, accentColor: 'var(--accent)', cursor: 'pointer' }}
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

function channelLabel(status: string): string {
  if (status === 'ok') return '已启用'
  if (status === 'unavailable') return '不可用'
  return '未启用'
}
