import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '@/lib/api'
import type { AppsResponse } from '@/types/api'

export function useApps() {
  return useQuery({
    queryKey: ['apps'] as const,
    queryFn: async () => {
      const body = await apiFetch<AppsResponse>('/v1/apps')
      return body.items
    },
    staleTime: 5 * 60_000,
    gcTime: 10 * 60_000,
  })
}
