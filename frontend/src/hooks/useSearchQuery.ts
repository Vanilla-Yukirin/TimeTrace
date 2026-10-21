import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '@/lib/api'
import type {
  ApiRecord,
  ChannelStatus,
  RecordsResponse,
  SearchByImageResponse,
  SearchResultItem,
  TextSearchItem,
  TextSearchResponse,
} from '@/types/api'

export interface SearchParams {
  q: string
  images: File[]
  visual: boolean
  semantic: boolean
  radius: number
  start: number | null
  end: number | null
  apps: string[]
  categories: string[]
  /** uuid / _unknown_device; null = all devices */
  device: string | null
  limit: number
}

export interface SearchState {
  items: SearchResultItem[]
  total: number
  visualChannel: ChannelStatus
  semanticChannel: ChannelStatus
  source: 'image' | 'text' | 'records'
}

function recordToResult(r: ApiRecord): SearchResultItem {
  return {
    screenshot_id: r.id,  // reuse record id as row key when no image channel
    record_id: r.id,
    ts_start: r.ts_start,
    ts_end: r.ts_end,
    app_name: r.app_name,
    window_title: r.window_title,
    url: r.url,
    thumb_path: r.thumb_path,
    vlm_desc: r.vlm_desc,
    category_final: r.category_final,
    device_id: r.device_id,
    match: {
      visual_distance: null,
      semantic_rank: null,
      text_rank: null,
      rrf_score: 0,
      reasons: [],
    },
  }
}

function textItemToResult(it: TextSearchItem): SearchResultItem {
  const reasons: string[] = []
  if (it.match.keyword_rank != null) reasons.push('关键词')
  if (it.match.semantic_rank != null) reasons.push('语义')
  return {
    screenshot_id: it.record_id,
    record_id: it.record_id,
    ts_start: it.ts_start,
    ts_end: it.ts_end,
    app_name: it.app_name,
    window_title: it.window_title,
    url: it.url,
    thumb_path: it.thumb_path,
    vlm_desc: it.vlm_desc,
    category_final: it.category_final,
    device_id: it.device_id ?? null,
    match: {
      visual_distance: null,
      semantic_rank: it.match.semantic_rank,
      text_rank: it.match.keyword_rank,
      rrf_score: it.match.rrf_score,
      reasons,
    },
  }
}

async function searchByImage(p: SearchParams): Promise<SearchState> {
  const fd = new FormData()
  p.images.forEach((f) => fd.append('images', f))
  fd.append('visual', String(p.visual))
  fd.append('semantic', String(p.semantic))
  fd.append('radius', String(p.radius))
  if (p.q) fd.append('q', p.q)
  if (p.start != null) fd.append('start', String(p.start))
  if (p.end != null) fd.append('end', String(p.end))
  if (p.apps.length) fd.append('apps', p.apps.join(','))
  if (p.categories.length) fd.append('categories', p.categories.join(','))
  if (p.device) fd.append('device_id', p.device)
  fd.append('limit', String(p.limit))

  // apiFetch leaves FormData's Content-Type alone (multipart boundary) and
  // routes 401 through the shared kick/redirect path.
  const body = await apiFetch<SearchByImageResponse>('/v1/search/by-image', {
    method: 'POST',
    body: fd,
  })
  return {
    items: body.items,
    total: body.total,
    visualChannel: body.visual_channel,
    semanticChannel: body.semantic_channel,
    source: 'image',
  }
}

/** Text query → the shared keyword + semantic retrieval (/v1/search/text),
 *  the same path the agent and MCP use. */
async function searchText(p: SearchParams): Promise<SearchState> {
  const sp = new URLSearchParams({ q: p.q, limit: String(p.limit) })
  if (p.start != null) sp.set('start', String(p.start))
  if (p.end != null) sp.set('end', String(p.end))
  if (p.apps.length) sp.set('apps', p.apps.join(','))
  if (p.categories.length) sp.set('categories', p.categories.join(','))
  if (p.device) sp.set('device_id', p.device)
  const body = await apiFetch<TextSearchResponse>(`/v1/search/text?${sp}`)
  return {
    items: body.items.map(textItemToResult),
    total: body.total,
    visualChannel: 'disabled',
    semanticChannel: body.semantic_channel,
    source: 'text',
  }
}

/** No query text — filters only (date / apps / categories / device). */
async function searchByRecords(p: SearchParams): Promise<SearchState> {
  const sp = new URLSearchParams({ limit: String(p.limit) })
  if (p.q) sp.set('q', p.q)
  if (p.start != null) sp.set('start', String(p.start))
  if (p.end != null) sp.set('end', String(p.end))
  if (p.apps.length) sp.set('apps', p.apps.join(','))
  if (p.categories.length) sp.set('categories', p.categories.join(','))
  if (p.device) sp.set('device_id', p.device)

  const body = await apiFetch<RecordsResponse>(`/v1/records?${sp}`)
  // Records are returned ts_start asc — flip so newest first in search view
  const items = [...body.items].reverse().map(recordToResult)
  return {
    items,
    total: items.length,
    visualChannel: 'disabled',
    semanticChannel: 'disabled',
    source: 'records',
  }
}

/** Stable string fingerprint for a `File[]` — `File` objects aren't JSON-serialisable,
 *  so TanStack Query's default hasher collapses them all to `{}` and different image
 *  sets end up colliding on the same cache key. Use metadata instead. */
function imagesKey(files: File[]): string {
  return files.map((f) => `${f.name}:${f.size}:${f.lastModified}`).join('|')
}

export function useSearchQuery(params: SearchParams | null) {
  const key = params && {
    q: params.q,
    images: imagesKey(params.images),
    visual: params.visual,
    semantic: params.semantic,
    radius: params.radius,
    start: params.start,
    end: params.end,
    apps: params.apps,
    categories: params.categories,
    device: params.device,
    limit: params.limit,
  }
  return useQuery({
    enabled: params !== null,
    queryKey: ['search', key] as const,
    queryFn: async () => {
      if (!params) throw new Error('no params')
      if (params.images.length > 0) return searchByImage(params)
      if (params.q.trim()) return searchText(params)
      return searchByRecords(params)
    },
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    retry: 1,
  })
}
