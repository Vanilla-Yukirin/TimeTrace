// Agent chat (SSE streaming) + AI insight dashboard (reports) API client.
// Mirrors lib/api.ts conventions: API_BASE prefix, cookie auth via credentials.

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export type AgentEvent =
  | { type: 'step'; phase: 'tool_call'; tool: string; args: Record<string, unknown> }
  | { type: 'step'; phase: 'tool_result'; tool: string; summary: string }
  | { type: 'token'; text: string }
  | { type: 'done'; records_consulted: number; model: string | null; tools_used: string[] }
  | { type: 'error'; message: string }

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

// POST /v1/agent/chat returns an SSE stream (text/event-stream). EventSource
// only does GET, so we read the body as a stream and split on the SSE record
// delimiter (\n\n), parsing each `data: {json}` line into an AgentEvent.
export async function* streamAgentChat(
  messages: ChatMessage[],
  opts?: { hoursBack?: number; signal?: AbortSignal },
): AsyncGenerator<AgentEvent> {
  const res = await fetch(`${API_BASE}/v1/agent/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ messages, hours_back: opts?.hoursBack }),
    signal: opts?.signal,
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
        yield JSON.parse(json) as AgentEvent
      } catch {
        // partial/garbled record — skip
      }
    }
  }
}

export interface Report {
  id: string
  scope: string
  period_start: number
  period_end: number
  format: 'html' | 'markdown'
  content: string
  model: string | null
  created_at: number
}

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
}
