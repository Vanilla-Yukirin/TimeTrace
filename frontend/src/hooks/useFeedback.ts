import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'

export function useFeedback() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: api.postFeedback,
    onSuccess: (_data, variables) => {
      // Invalidate the list (all cached days) and the specific record detail
      qc.invalidateQueries({ queryKey: ['records'] })
      qc.invalidateQueries({ queryKey: queryKeys.record(variables.record_id) })
    },
  })
}
