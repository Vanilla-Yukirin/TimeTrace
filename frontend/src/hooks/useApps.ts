import { useQuery } from '@tanstack/react-query'
import type { AppsResponse } from '@/types/api'

export function useApps() {
  return useQuery({
    queryKey: ['apps'] as const,
    queryFn: async () => {
      const res = await fetch('/v1/apps')
      if (!res.ok) throw new Error(`GET /v1/apps failed: ${res.status}`)
      const body = await res.json() as AppsResponse
      return body.items
    },
    staleTime: 5 * 60_000,
    gcTime: 10 * 60_000,
  })
}
