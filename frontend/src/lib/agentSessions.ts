// Client-side persistence for agent chat: multiple conversations kept in
// localStorage so they survive a refresh (no server-side chat table yet — this
// is the single-user, local-first store). Also the ordered-block message model
// the UI renders: an assistant turn is a SEQUENCE of blocks (thinking / text /
// tool) in arrival order, so text that comes between two tool calls renders as
// its own bubble instead of being merged into one.

import type { Usage } from './agentApi'

export type AssistantBlock =
  | { kind: 'text'; text: string }
  | { kind: 'thinking'; text: string }
  | { kind: 'tool'; tool: string; args?: Record<string, unknown>; summary?: string }

export interface UserTurn {
  role: 'user'
  content: string
}

export interface AssistantTurn {
  role: 'assistant'
  blocks: AssistantBlock[]
  usage?: Usage
  toolCount: number
  error?: string
  pending?: boolean
}

export type ChatTurn = UserTurn | AssistantTurn

export interface AgentSession {
  id: string
  title: string
  createdAt: number
  updatedAt: number
  turns: ChatTurn[]
}

const KEY = 'tt_agent_sessions'
const MAX_SESSIONS = 100

function uid(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

export function newSession(): AgentSession {
  const now = Date.now()
  return { id: uid(), title: '新对话', createdAt: now, updatedAt: now, turns: [] }
}

export function loadSessions(): AgentSession[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    const sessions = parsed as AgentSession[]
    // An interrupted stream may have left a turn flagged pending; clear it so a
    // reloaded conversation doesn't show a frozen "思考中" cursor.
    for (const s of sessions) {
      for (const t of s.turns ?? []) {
        if (t.role === 'assistant' && t.pending) t.pending = false
      }
    }
    return sessions
  } catch {
    return []
  }
}

export function saveSessions(sessions: AgentSession[]): void {
  try {
    const trimmed = [...sessions]
      .filter((s) => s.turns.length > 0)
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, MAX_SESSIONS)
    localStorage.setItem(KEY, JSON.stringify(trimmed))
  } catch {
    // Quota exceeded / storage disabled — best-effort; chat still works in-memory.
  }
}

/** First user message, trimmed to a short session title. */
export function deriveTitle(turns: ChatTurn[]): string {
  const firstUser = turns.find((t): t is UserTurn => t.role === 'user')
  if (!firstUser) return '新对话'
  const t = firstUser.content.trim().replace(/\s+/g, ' ')
  if (!t) return '新对话'
  return t.length > 24 ? `${t.slice(0, 24)}…` : t
}

// ---- token accounting (lite: derived from locally-stored agent chats) -------
export interface TokenStats {
  inputTokens: number
  outputTokens: number
  totalTokens: number
  toolCalls: number
  messages: number
}

const ZERO: TokenStats = {
  inputTokens: 0,
  outputTokens: 0,
  totalTokens: 0,
  toolCalls: 0,
  messages: 0,
}

export function statsForTurns(turns: ChatTurn[]): TokenStats {
  const s: TokenStats = { ...ZERO }
  for (const t of turns) {
    if (t.role !== 'assistant') continue
    s.messages += 1
    s.toolCalls += t.toolCount || 0
    if (t.usage) {
      s.inputTokens += t.usage.prompt_tokens || 0
      s.outputTokens += t.usage.completion_tokens || 0
      s.totalTokens += t.usage.total_tokens || 0
    }
  }
  return s
}

export function statsForSessions(sessions: AgentSession[]): TokenStats {
  const total: TokenStats = { ...ZERO }
  for (const sess of sessions) {
    const s = statsForTurns(sess.turns)
    total.inputTokens += s.inputTokens
    total.outputTokens += s.outputTokens
    total.totalTokens += s.totalTokens
    total.toolCalls += s.toolCalls
    total.messages += s.messages
  }
  return total
}
