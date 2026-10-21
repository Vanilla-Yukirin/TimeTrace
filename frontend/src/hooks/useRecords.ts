import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { dayRange, toDateParam } from '@/lib/dateUtils'
import type { ApiRecord, RecordsResponse } from '@/types/api'

const MAX_PAGES = 20  // 上限 10000 条，防止异常 cursor 导致无限循环

async function fetchAllRecords(start: number, end: number, device: string | null): Promise<ApiRecord[]> {
  const items: ApiRecord[] = []
  let cursor: string | null = null
  let pages = 0
  do {
    const p = new URLSearchParams({ start: String(start), end: String(end), limit: '500' })
    if (cursor) p.set('cursor', cursor)
    if (device) p.set('device_id', device)
    // Via apiFetch so a mid-session 401 throws UnauthorizedError + broadcasts
    // the cross-tab kick (and the QueryClient onError redirects this tab),
    // rather than surfacing as an opaque error on a stuck authed page.
    const page = await apiFetch<RecordsResponse>(`/v1/records?${p}`)
    items.push(...page.items)
    cursor = page.next_cursor
    pages++
  } while (cursor && pages < MAX_PAGES)
  return items
}

/** All records of ``date``; ``device`` (uuid or _unknown_device) narrows to one device. */
export function useRecords(date: Date, device: string | null = null) {
  const [start, end] = dayRange(date)
  const dateStr = toDateParam(date)
  // A past day no longer changes (barring late outbox uploads, picked up after
  // staleTime), so only today polls — and only while the tab is visible.
  // Re-pulling a whole day every 30s re-rendered the timeline for nothing.
  const isPast = () => end < Date.now()
  return useQuery({
    queryKey: queryKeys.records(dateStr, device),
    queryFn: () => fetchAllRecords(start, end, device),
    staleTime: () => (isPast() ? 10 * 60_000 : 30_000),
    refetchInterval: () => (isPast() ? false : 60_000),
    refetchIntervalInBackground: false,
    gcTime: 5 * 60_000,
    retry: 2,
  })
}
