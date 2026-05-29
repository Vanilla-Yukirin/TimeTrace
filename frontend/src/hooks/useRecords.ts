import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { dayRange, toDateParam } from '@/lib/dateUtils'
import type { ApiRecord, RecordsResponse } from '@/types/api'

const MAX_PAGES = 20  // 上限 10000 条，防止异常 cursor 导致无限循环

async function fetchAllRecords(start: number, end: number): Promise<ApiRecord[]> {
  const items: ApiRecord[] = []
  let cursor: string | null = null
  let pages = 0
  do {
    const p = new URLSearchParams({ start: String(start), end: String(end), limit: '500' })
    if (cursor) p.set('cursor', cursor)
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

export function useRecords(date: Date) {
  const [start, end] = dayRange(date)
  const dateStr = toDateParam(date)
  return useQuery({
    queryKey: queryKeys.records(dateStr),
    queryFn: () => fetchAllRecords(start, end),
    staleTime: 30_000,
    refetchInterval: 30_000,
    gcTime: 5 * 60_000,
    retry: 2,
  })
}
