export const queryKeys = {
  records: (dateStr: string) => ['records', dateStr] as const,
  record: (id: string) => ['record', id] as const,
  audit: (limit: number) => ['audit', limit] as const,
  llmRequests: (caller: string | null, limit: number) => ['llm-requests', caller, limit] as const,
  llmStats: () => ['llm-stats'] as const,
  summariesDay: (day: string) => ['summaries-day', day] as const,
  categories: () => ['categories'] as const,
  runtimeInfo: () => ['runtime-info'] as const,
  authMe: () => ['auth', 'me'] as const,
  adminTokens: () => ['admin', 'tokens'] as const,
  appOverrides: () => ['settings', 'app-overrides'] as const,
}
