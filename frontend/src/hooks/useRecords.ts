import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { dayRange, toDateParam } from '@/lib/dateUtils'

const MAX_PAGES = 20  // 上限 10000 条，防止异常 cursor 导致无限循环

async function fetchAllRecords(start: number, end: number): Promise<import('@/types/api').ApiRecord[]> {
  const items: import('@/types/api').ApiRecord[] = []
  let cursor: string | null = null
  let pages = 0
  do {
    const p = new URLSearchParams({ start: String(start), end: String(end), limit: '500' })
    if (cursor) p.set('cursor', cursor)
    const res = await fetch(`/v1/records?${p}`)
    if (!res.ok) throw new Error(`GET /v1/records failed: ${res.status} ${res.statusText}`)
    const page = await res.json() as import('@/types/api').RecordsResponse
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
