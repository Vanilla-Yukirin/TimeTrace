import type {
  ApiRecordDetail,
  AuthMe,
  CategoriesResponse,
  ChangePasswordRequest,
  FeedbackRequest,
  FeedbackResponse,
  LoginRequest,
  LoginResponse,
  RecordsResponse,
  RuntimeInfo,
  TokenCreated,
  TokenSummary,
} from '@/types/api'

/** Returned by ``apiFetch`` when the server says 401. Callers in
 *  ``AuthContext`` listen for this so they can clear local state +
 *  redirect to /login + broadcast the kick across browser tabs. */
export class UnauthorizedError extends Error {
  constructor(public readonly path: string) {
    super(`401 unauthorized: ${path}`)
    this.name = 'UnauthorizedError'
  }
}

/** Broadcast a 401 to other tabs via localStorage; the value is just a
 *  timestamp so two close-in-time kicks both fire the storage event. */
function broadcastKick(): void {
  try {
    localStorage.setItem('tt_auth_kicked', String(Date.now()))
  } catch {
    // localStorage can be unavailable (private mode iframe); not fatal.
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    // Send the HttpOnly tt_session cookie on every request. Same-origin in
    // production (front + back at timetrace.yukirin.me), Vite proxy in dev
    // (5173 → 8765 — proxy forwards cookies natively).
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
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
}
