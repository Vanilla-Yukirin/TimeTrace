import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { dayRange, toDateParam } from '@/lib/dateUtils'

export function useRecords(date: Date) {
  const [start, end] = dayRange(date)
  const dateStr = toDateParam(date)
  return useQuery({
    queryKey: queryKeys.records(dateStr),
    queryFn: () => api.getRecords({ start, end, limit: 500 }).then(r => r.items),
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    retry: 2,
  })
}
