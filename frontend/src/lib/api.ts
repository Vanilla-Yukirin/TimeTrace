import type {
  ApiRecordDetail,
  AppOverrides,
  AuditResponse,
  AuthMe,
  CategoriesResponse,
  ChangePasswordRequest,
  FeedbackRequest,
  FeedbackResponse,
  LlmRequestsResponse,
  LlmStats,
  LoginRequest,
  LoginResponse,
  RecordsResponse,
  RuntimeInfo,
  SummariesDay,
  TokenCreated,
  TokenSummary,
} from '@/types/api'

/** Returned by ``apiFetch`` when the server says 401. Callers in
 *  ``AuthContext`` listen for this so they can clear local state +
 *  redirect to /login + broadcast the kick across browser tabs. */
export class UnauthorizedError extends Error {
  // Plain field + explicit assignment instead of a constructor parameter
  // property — the latter is disallowed under tsconfig `erasableSyntaxOnly`.
  readonly path: string
  constructor(path: string) {
    super(`401 unauthorized: ${path}`)
    this.path = path
    this.name = 'UnauthorizedError'
  }
}

// Monotonic counter so each broadcast writes a DISTINCT value. A 'storage'
// event only fires when the stored value actually CHANGES, so two kicks in the
// same millisecond would otherwise collide (the 2nd setItem = no-op, no event).
let _kickSeq = 0

/** Broadcast a logout/401 to OTHER tabs via a localStorage 'storage' event so
 *  they clear their auth state too. Exported so the explicit logout flow can
 *  fire it (a 204 logout doesn't go through the 401 path). Note: the writing
 *  tab never receives its own 'storage' event — it must clear state locally. */
export function broadcastKick(): void {
  try {
    localStorage.setItem('tt_auth_kicked', `${Date.now()}-${++_kickSeq}`)
  } catch {
    // localStorage can be unavailable (private mode iframe); not fatal.
  }
}

/** The ONE fetch wrapper every API call must go through. Centralizes:
 *  - credentials:'include' (send the HttpOnly session cookie)
 *  - 401 → broadcastKick() + UnauthorizedError (so AuthContext/RequireAuth
 *    can react uniformly; see the QueryClient onError handler in App.tsx)
 *  - JSON Content-Type ONLY for string bodies (FormData must set its own
 *    multipart boundary — never force application/json on it)
 *  Exported so data hooks (useRecords/useApps/useSearchQuery) participate in
 *  the same 401-recovery path instead of hand-rolling raw fetch().
 */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const isStringBody = typeof init?.body === 'string'
  const baseHeaders: Record<string, string> = isStringBody
    ? { 'Content-Type': 'application/json' }
    : {}
  const res = await fetch(path, {
    // Same-origin in prod (front + back at timetrace.yukirin.me), Vite proxy
    // in dev (5173 → 8765 — proxy forwards cookies natively).
    credentials: 'include',
    ...init,
    headers: { ...baseHeaders, ...(init?.headers ?? {}) },
  })
  if (res.status === 401) {
    broadcastKick()
    throw new UnauthorizedError(path)
  }
  if (res.status === 204) {
    // No content — caller types T should be void / undefined here.
    return undefined as T
  }
  if (!res.ok) {
    // Try to surface the FastAPI ``detail`` field if present.
    let detail: string | undefined
    try {
      const body = await res.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      // Non-JSON body; fall through to generic message.
    }
    throw new Error(`API ${path} failed: ${res.status} ${detail ?? res.statusText}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  // ---- business routes (Phase 4 gated) ---------------------------------
  getRecords: (params: { start: number; end: number; limit?: number; app?: string; q?: string }) => {
    const p = new URLSearchParams({
      start: String(params.start),
      end: String(params.end),
      limit: String(params.limit ?? 500),
    })
    if (params.app) p.set('app', params.app)
    if (params.q) p.set('q', params.q)
    return apiFetch<RecordsResponse>(`/v1/records?${p}`)
  },

  getRecord: (id: string) =>
    apiFetch<ApiRecordDetail>(`/v1/records/${encodeURIComponent(id)}`),

  // Audit-log feed: newest-first records + pipeline status/latencies/flags.
  getAuditRecords: (params: { limit?: number; cursor?: string }) => {
    const p = new URLSearchParams({ limit: String(params.limit ?? 50) })
    if (params.cursor) p.set('cursor', params.cursor)
    return apiFetch<AuditResponse>(`/v1/audit/records?${p}`)
  },

  // Unified LLM-request ledger (/llm-log panel).
  getLlmRequests: (params: {
    caller?: string
    status?: string
    before_ts?: number
    limit?: number
  }) => {
    const p = new URLSearchParams({ limit: String(params.limit ?? 100) })
    if (params.caller) p.set('caller', params.caller)
    if (params.status) p.set('status', params.status)
    if (params.before_ts != null) p.set('before_ts', String(params.before_ts))
    return apiFetch<LlmRequestsResponse>(`/v1/llm-requests?${p}`)
  },

  getLlmStats: () => apiFetch<LlmStats>('/v1/llm-requests/stats'),

  // Memory-pyramid day view (/pyramid panel): all grains for one logical day.
  getSummariesForDay: (day?: string) =>
    apiFetch<SummariesDay>(`/v1/summaries${day ? `?day=${encodeURIComponent(day)}` : ''}`),

  getCategories: () =>
    apiFetch<CategoriesResponse>('/v1/categories'),

  postFeedback: (body: FeedbackRequest) =>
    apiFetch<FeedbackResponse>('/v1/feedback', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  getRuntimeInfo: () =>
    apiFetch<RuntimeInfo>('/v1/runtime-info'),

  healthz: () =>
    apiFetch<{ status: string }>('/healthz'),

  // ---- auth (Phase 5) --------------------------------------------------
  login: (body: LoginRequest) =>
    apiFetch<LoginResponse>('/v1/auth/login', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  logout: () =>
    apiFetch<void>('/v1/auth/logout', { method: 'POST' }),

  me: () =>
    apiFetch<AuthMe>('/v1/auth/me'),

  changePassword: (body: ChangePasswordRequest) =>
    apiFetch<void>('/v1/auth/change-password', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  // ---- admin tokens (Phase 6) ------------------------------------------
  listTokens: () =>
    apiFetch<TokenSummary[]>('/v1/admin/tokens'),

  createToken: (label: string) =>
    apiFetch<TokenCreated>('/v1/admin/tokens', {
      method: 'POST',
      body: JSON.stringify({ label }),
    }),

  revokeToken: (label: string) =>
    apiFetch<void>(`/v1/admin/tokens/${encodeURIComponent(label)}`, {
      method: 'DELETE',
    }),

  // ---- settings: per-app classification overrides ----------------------
  getAppOverrides: () =>
    apiFetch<AppOverrides>('/v1/settings/app-overrides'),

  putAppOverrides: (body: AppOverrides) =>
    apiFetch<AppOverrides>('/v1/settings/app-overrides', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
}
