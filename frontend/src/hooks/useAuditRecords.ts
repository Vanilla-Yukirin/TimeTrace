import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'

/** Live audit feed: the newest ``limit`` records, polled for a "watch it move"
 *  feel. Only this newest page polls (Phase A has no older-page pagination UI),
 *  ``refetchIntervalInBackground:false`` stops the poll when the tab is hidden,
 *  and ``keepPreviousData`` prevents the table flickering empty on each refetch.
 *  The 4s cadence is "live enough" to watch a record march
 *  captured→processing→done without hammering the single-locked DB. */
export function useAuditRecords(limit: number) {
  return useQuery({
    queryKey: queryKeys.audit(limit),
    queryFn: () => api.getAuditRecords({ limit }),
    refetchInterval: 4000,
    refetchIntervalInBackground: false,
    staleTime: 2000,
    gcTime: 5 * 60_000,
    retry: 1,
    placeholderData: keepPreviousData,
  })
}
