// Agent chat (SSE streaming) + AI insight dashboard (reports) API client.
// Mirrors lib/api.ts conventions: API_BASE prefix, cookie auth via credentials.

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export interface Usage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export type AgentEvent =
  | { type: 'step'; phase: 'tool_call'; tool: string; args: Record<string, unknown> }
  | { type: 'step'; phase: 'tool_result'; tool: string; summary: string }
  | { type: 'reasoning'; text: string }
  | { type: 'token'; text: string }
  | { type: 'done'; records_consulted: number; model: string | null; tools_used: string[]; usage?: Usage }
  | { type: 'error'; message: string }

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

// Shared SSE-over-fetch reader: POST a JSON body, read text/event-stream,
// split on the record delimiter (\n\n), yield each parsed `data: {json}` event.
// EventSource only does GET, so we hand-roll this for POST endpoints.
async function* postSSE<T>(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => '')
    throw new Error(`API ${res.status}: ${text || res.statusText}`)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const record = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      const dataLine = record.split('\n').find((l) => l.startsWith('data:'))
      if (!dataLine) continue
      const json = dataLine.slice(5).trim()
      if (!json) continue
      try {
        yield JSON.parse(json) as T
      } catch {
        // partial/garbled record — skip
      }
    }
  }
}

// POST /v1/agent/chat → SSE stream of AgentEvents.
export function streamAgentChat(
  messages: ChatMessage[],
  opts?: { hoursBack?: number; signal?: AbortSignal },
): AsyncGenerator<AgentEvent> {
  return postSSE<AgentEvent>(
    '/v1/agent/chat',
    { messages, hours_back: opts?.hoursBack },
    opts?.signal,
  )
}

export interface Report {
  id: string
  scope: string
  period_start: number
  period_end: number
  format: 'html' | 'markdown' | 'json'
  content: string
  model: string | null
  created_at: number
}

// Parsed shape of a format:'json' report's `content`. The LLM only supplies the
// text fields; colour + layout are the frontend's themed <ReportView>, so reports
// adapt to light/dark instead of the model baking (often-wrong) inline colours.
export interface ReportInsight {
  emoji: string
  title: string
  body: string
}

export interface ReportData {
  scope_label: string
  headline: string
  headline_caption: string
  top_app: { name: string; value: string } | null
  caveat: string | null
  insights: ReportInsight[]
}

// Defensively parse a json-report's content (the backend already validates, but
// we re-check the shape). Returns null if it isn't renderable → caller falls back.
export function parseReportData(content: string): ReportData | null {
  let o: Record<string, unknown>
  try {
    const parsed: unknown = JSON.parse(content)
    if (!parsed || typeof parsed !== 'object') return null
    o = parsed as Record<string, unknown>
  } catch {
    return null
  }
  const str = (v: unknown): string => (typeof v === 'string' ? v : '')
  const top = o.top_app as Record<string, unknown> | null | undefined
  const topApp =
    top && typeof top === 'object' && str(top.name)
      ? { name: str(top.name), value: str(top.value) }
      : null
  const insights: ReportInsight[] = Array.isArray(o.insights)
    ? (o.insights as unknown[])
        .filter((x): x is Record<string, unknown> => !!x && typeof x === 'object')
        .filter((x) => str(x.title) && str(x.body))
        .map((x) => ({ emoji: str(x.emoji) || '•', title: str(x.title), body: str(x.body) }))
    : []
  if (!str(o.headline) && insights.length === 0) return null // nothing renderable
  return {
    scope_label: str(o.scope_label),
    headline: str(o.headline),
    headline_caption: str(o.headline_caption),
    top_app: topApp,
    caveat: str(o.caveat) || null,
    insights,
  }
}

// Events from POST /v1/reports/generate/stream — the agent's tool steps + live
// report HTML tokens, terminated by either the persisted report or an error.
export type ReportEvent =
  | { type: 'step'; phase: 'tool_call'; tool: string; args: Record<string, unknown> }
  | { type: 'step'; phase: 'tool_result'; tool: string; summary: string }
  | { type: 'token'; text: string }
  | { type: 'report'; report: Report }
  | { type: 'error'; message: string }

export const reportsApi = {
  async latest(scope: string): Promise<Report | null> {
    const res = await fetch(
      `${API_BASE}/v1/reports/latest?scope=${encodeURIComponent(scope)}`,
      { credentials: 'include' },
    )
    if (res.status === 404) return null
    if (!res.ok) {
      const text = await res.text().catch(() => '')
      throw new Error(`API ${res.status}: ${text || res.statusText}`)
    }
    return res.json() as Promise<Report>
  },

  async generate(scope: string): Promise<Report> {
    const res = await fetch(`${API_BASE}/v1/reports/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ scope }),
    })
    if (!res.ok) {
      const text = await res.text().catch(() => '')
      throw new Error(`API ${res.status}: ${text || res.statusText}`)
    }
    return res.json() as Promise<Report>
  },

  // POST /v1/reports/generate/stream → SSE of ReportEvents (tool steps + live
  // token output + terminal report). Lets the UI show progress for the slow,
  // token-costly generation instead of a blank spinner.
  generateStream(scope: string, signal?: AbortSignal): AsyncGenerator<ReportEvent> {
    return postSSE<ReportEvent>('/v1/reports/generate/stream', { scope }, signal)
  },
}
