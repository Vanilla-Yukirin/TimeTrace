export const queryKeys = {
  records: (dateStr: string) => ['records', dateStr] as const,
  record: (id: string) => ['record', id] as const,
  categories: () => ['categories'] as const,
  runtimeInfo: () => ['runtime-info'] as const,
  authMe: () => ['auth', 'me'] as const,
  adminTokens: () => ['admin', 'tokens'] as const,
  appOverrides: () => ['settings', 'app-overrides'] as const,
}
