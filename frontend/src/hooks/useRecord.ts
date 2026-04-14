import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'

export function useRecord(id: string | null) {
  return useQuery({
    queryKey: queryKeys.record(id ?? ''),
    queryFn: () => api.getRecord(id!),
    enabled: id !== null,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
  })
}
