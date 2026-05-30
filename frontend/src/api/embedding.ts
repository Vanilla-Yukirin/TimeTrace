// Embedding-server (timetrace-embserver, port 8766) client.
//
// This is a SEPARATE local service from the main API: it authenticates with a
// machine bearer key (tt_emb_*), NOT the session cookie. So these calls do NOT
// go through api/client.ts (which forces credentials:'include' + same-origin
// /v1). They hit the vite-proxied /emb prefix -> 8766, with an explicit
// Authorization header supplied by the user (stored in localStorage).

const EMB_BASE = '/emb'
const KEY_STORAGE = 'tt_emb_key'

export type EmbError = { status: number; detail: string }

export function getEmbKey(): string {
  return localStorage.getItem(KEY_STORAGE) ?? ''
}

export function setEmbKey(key: string): void {
  if (key) localStorage.setItem(KEY_STORAGE, key)
  else localStorage.removeItem(KEY_STORAGE)
}

async function embRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const key = getEmbKey()
  const res = await fetch(`${EMB_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${key}`,
      ...(init?.headers ?? {}),
    },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch {
      // non-JSON error body; keep statusText
    }
    return Promise.reject<T>({ status: res.status, detail } as EmbError)
  }
  return res.json() as Promise<T>
}

export type EmbStatus = {
  loaded: boolean
  model: string
  dtype: string
  loaded_at: number | null
  last_used: number | null
  idle_seconds: number | null
  idle_ttl_seconds: number
  vram_mb: number | null
}

export type SelftestVector = { label: string; cosine: number }

export type SelftestResult = {
  dtype: string
  model: string
  dim: number
  ref_dtype: string | null
  per_vector: SelftestVector[]
  min_cosine: number
  matrix_max_abs_diff_vs_ref: number
  matrix_max_abs_diff_vs_official: number
  threshold: number
  verdict: 'PASS' | 'FAIL'
}

export function getEmbStatus(): Promise<EmbStatus> {
  return embRequest<EmbStatus>('/admin/status')
}

export function loadEmbModel(dtype: string): Promise<EmbStatus> {
  return embRequest<EmbStatus>('/admin/load', {
    method: 'POST',
    body: JSON.stringify({ dtype }),
  })
}

export function runEmbSelftest(threshold = 0.999): Promise<SelftestResult> {
  return embRequest<SelftestResult>('/admin/selftest', {
    method: 'POST',
    body: JSON.stringify({ threshold }),
  })
}
